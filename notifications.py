"""Send alerts when ECS service health checks fail (e.g. HTTP != 200)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def should_notify(
    report: Dict[str, Any],
    *,
    on_fail: bool = True,
    on_warn: bool = False,
) -> bool:
    summary = report.get("summary", {})
    if on_fail and (summary.get("failed", 0) > 0 or summary.get("critical_failed", 0) > 0):
        return True
    if on_warn and summary.get("warnings", 0) > 0:
        return True
    if report.get("account_check", {}).get("status") == "FAIL":
        return True
    return False


def build_alert_payload(report: Dict[str, Any]) -> Dict[str, Any]:
    unhealthy = [
        item
        for item in report.get("results", [])
        if item.get("status") in {"FAIL", "WARN"}
    ]

    services: List[Dict[str, Any]] = []
    for item in unhealthy:
        checks = item.get("checks", {})
        http_check = checks.get("http_health", {})
        services.append(
            {
                "cluster": item.get("cluster"),
                "service": item.get("service"),
                "status": item.get("status"),
                "critical": item.get("critical", False),
                "http_status": http_check.get("http_status"),
                "http_url": http_check.get("url"),
                "http_message": http_check.get("message"),
                "summary": _service_issue_summary(item),
            }
        )

    summary = report.get("summary", {})
    return {
        "tool": report.get("tool", "ecs-service-doctor"),
        "version": report.get("version"),
        "generated_at": report.get("generated_at", utc_now()),
        "region": report.get("region"),
        "account_id": report.get("account_check", {}).get("actual_account_id"),
        "summary": summary,
        "unhealthy_services": services,
        "title": _alert_title(summary, services),
        "text": _alert_text(report, services),
    }


def _service_issue_summary(item: Dict[str, Any]) -> str:
    checks = item.get("checks", {})
    preferred_keys = (
        "http_health",
        "host_header_health",
        "target_group_health",
        "task_counts",
        "deployments",
    )
    for key in preferred_keys:
        check = checks.get(key) or {}
        if check.get("status") in {"FAIL", "WARN"} and check.get("message"):
            return str(check["message"])
    if item.get("error"):
        return str(item["error"])
    return f"Service status {item.get('status')}"


def _alert_title(summary: Dict[str, Any], services: List[Dict[str, Any]]) -> str:
    failed = summary.get("failed", 0)
    warnings = summary.get("warnings", 0)
    names = ", ".join(f"{s['cluster']}/{s['service']}" for s in services[:3])
    if len(services) > 3:
        names += f" (+{len(services) - 3} more)"
    if failed:
        return f"ECS alert: {failed} service(s) unhealthy — {names}"
    return f"ECS alert: {warnings} service(s) need attention — {names}"


def _alert_text(report: Dict[str, Any], services: List[Dict[str, Any]]) -> str:
    lines = [
        _alert_title(report.get("summary", {}), services),
        f"Region: {report.get('region')}",
        f"Account: {report.get('account_check', {}).get('actual_account_id', 'unknown')}",
        f"Time: {report.get('generated_at', utc_now())}",
        "",
    ]
    for item in services:
        http_bit = ""
        if item.get("http_status") is not None:
            http_bit = f" HTTP {item['http_status']}"
            if item.get("http_url"):
                http_bit += f" ({item['http_url']})"
        lines.append(
            f"- [{item['status']}] {item['cluster']}/{item['service']}{http_bit}: "
            f"{item['summary']}"
        )
    return "\n".join(lines)


def send_slack_webhook(webhook_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    body = {
        "text": payload["text"],
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": payload["title"][:150]},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```{payload['text'][:2800]}```"},
            },
        ],
    }
    return _post_json(webhook_url, body, channel="slack")


def send_teams_webhook(webhook_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Post to a Microsoft Teams incoming webhook or Power Automate workflow."""
    failed = (payload.get("summary") or {}).get("failed", 0)
    facts = [
        {"name": "Region", "value": str(payload.get("region") or "—")},
        {"name": "Account", "value": str(payload.get("account_id") or "—")},
        {"name": "Time", "value": str(payload.get("generated_at") or utc_now())},
    ]
    for item in payload.get("unhealthy_services", [])[:8]:
        name = f"{item.get('cluster')}/{item.get('service')}"
        facts.append({"name": name, "value": str(item.get("summary") or item.get("status"))})

    body = {
        "text": payload["text"][:4000],
        "title": payload["title"][:150],
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": payload["title"][:150],
        "themeColor": "E11D48" if failed else "F59E0B",
        "sections": [
            {
                "activityTitle": payload["title"][:150],
                "facts": facts,
            }
        ],
    }
    return _post_json(webhook_url, body, channel="teams")


def send_generic_webhook(webhook_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return _post_json(webhook_url, payload, channel="webhook")


def send_sns(session, topic_arn: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    sns = session.client("sns")
    response = sns.publish(
        TopicArn=topic_arn,
        Subject=payload["title"][:100],
        Message=json.dumps(payload, indent=2, default=str),
    )
    return {
        "channel": "sns",
        "ok": True,
        "message_id": response.get("MessageId"),
    }


def _post_json(url: str, body: Dict[str, Any], channel: str) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return {
                "channel": channel,
                "ok": True,
                "status": getattr(response, "status", 200),
            }
    except urllib.error.HTTPError as exc:
        return {
            "channel": channel,
            "ok": False,
            "status": exc.code,
            "error": exc.reason,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "channel": channel,
            "ok": False,
            "error": str(exc),
        }


def dispatch_notifications(
    report: Dict[str, Any],
    notifications_config: Dict[str, Any],
    session=None,
) -> List[Dict[str, Any]]:
    if not should_notify(
        report,
        on_fail=notifications_config.get("on_fail", True),
        on_warn=notifications_config.get("on_warn", False),
    ):
        return []

    payload = build_alert_payload(report)
    results: List[Dict[str, Any]] = []

    slack_url = notifications_config.get("slack_webhook_url")
    if slack_url:
        results.append(send_slack_webhook(slack_url, payload))

    teams_url = notifications_config.get("teams_webhook_url")
    if teams_url:
        results.append(send_teams_webhook(teams_url, payload))

    webhook_url = notifications_config.get("webhook_url")
    if webhook_url:
        results.append(send_generic_webhook(webhook_url, payload))

    topic_arn = notifications_config.get("sns_topic_arn")
    if topic_arn and session is not None:
        try:
            results.append(send_sns(session, topic_arn, payload))
        except Exception as exc:  # noqa: BLE001
            results.append({"channel": "sns", "ok": False, "error": str(exc)})

    return results


def fingerprint_unhealthy(report: Dict[str, Any]) -> str:
    parts = []
    for item in report.get("results", []):
        if item.get("status") not in {"FAIL", "WARN"}:
            continue
        http = (item.get("checks") or {}).get("http_health") or {}
        hosts = (item.get("checks") or {}).get("host_header_health") or {}
        parts.append(
            f"{item.get('cluster')}/{item.get('service')}:"
            f"{item.get('status')}:{http.get('http_status')}:{http.get('message')}:"
            f"{hosts.get('message')}"
        )
    return "|".join(sorted(parts))
