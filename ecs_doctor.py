#!/usr/bin/env python3

"""
ecs-service-doctor

A practical ECS health checker that validates ECS service stability beyond
desired/running task counts.

Checks:
- AWS account safety
- ECS service desired/running/pending counts
- rollout state
- multiple active deployments
- recent ECS service events
- task definition image
- load balancer target group health

Usage:
    python ecs_doctor.py --cluster my-cluster --service my-api
    python ecs_doctor.py --cluster my-cluster --all-services
    python ecs_doctor.py --config config.json
    python ecs_doctor.py --config config.json --json
"""

import argparse
import html
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError, ProfileNotFound


VERSION = "0.4.0"
STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"
ECS_DESCRIBE_BATCH_SIZE = 10
DEFAULT_MAX_WORKERS = 10

TARGET_HEALTH_COUNTS = (
    "healthy",
    "unhealthy",
    "initial",
    "draining",
    "unused",
    "unavailable",
    "unknown",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def resolve_region(session: boto3.Session, aws_config: Dict[str, Any]) -> str:
    if aws_config.get("region"):
        return aws_config["region"]

    for candidate in (
        session.region_name,
        os.environ.get("AWS_DEFAULT_REGION"),
        os.environ.get("AWS_REGION"),
    ):
        if candidate:
            return candidate

    return "us-east-1"


def create_session(config: Dict[str, Any]) -> boto3.Session:
    aws_config = config.setdefault("aws", {})
    profile = aws_config.get("profile")
    region = aws_config.get("region")

    try:
        if profile:
            session = boto3.Session(profile_name=profile, region_name=region)
        else:
            session = boto3.Session(region_name=region)
    except ProfileNotFound as exc:
        raise SystemExit(f"ERROR: AWS profile not found: {profile}") from exc

    aws_config["region"] = resolve_region(session, aws_config)
    return session


def normalize_service_entry(entry: Any) -> Dict[str, Any]:
    if isinstance(entry, str):
        return {"name": entry}
    if isinstance(entry, dict) and entry.get("name"):
        return entry
    raise ValueError(f"Invalid service entry: {entry!r}")


def normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Accept minimal or full config and produce a consistent internal shape."""
    config: Dict[str, Any] = {
        "aws": dict(raw.get("aws", {})),
        "checks": dict(raw.get("checks", {})),
        "clusters": [],
    }

    for key in ("region", "profile", "expected_account_id", "max_workers"):
        if key in raw and key not in config["aws"]:
            config["aws"][key] = raw[key]

    if "clusters" in raw:
        for cluster in raw["clusters"]:
            config["clusters"].append(
                {
                    "name": cluster["name"],
                    "services": [
                        normalize_service_entry(service)
                        for service in cluster.get("services", [])
                    ],
                }
            )
        return config

    if "cluster" in raw:
        services = raw.get("services", [])
        if not services:
            raise ValueError('Minimal config needs a "services" list')

        config["clusters"].append(
            {
                "name": raw["cluster"],
                "services": [normalize_service_entry(service) for service in services],
            }
        )
        return config

    raise ValueError(
        'Config must include "cluster" + "services", or a "clusters" array. '
        "Or skip config and use: --cluster NAME --service NAME"
    )


def build_config_from_cli(args: argparse.Namespace) -> Dict[str, Any]:
    aws: Dict[str, Any] = {}
    if args.region:
        aws["region"] = args.region
    if args.profile:
        aws["profile"] = args.profile
    if args.account:
        aws["expected_account_id"] = args.account

    return {
        "aws": aws,
        "checks": {},
        "clusters": [{"name": args.cluster, "services": []}],
        "_cli_all_services": args.all_services,
        "_cli_service_names": args.service or [],
    }


def list_services_in_cluster(ecs_client, cluster_name: str) -> List[str]:
    names: List[str] = []
    paginator = ecs_client.get_paginator("list_services")

    for page in paginator.paginate(cluster=cluster_name):
        for arn in page.get("serviceArns", []):
            names.append(arn.rsplit("/", 1)[-1])

    return names


def resolve_input_config(args: argparse.Namespace) -> Dict[str, Any]:
    if args.config:
        config = normalize_config(load_config(args.config))
    elif args.cluster and (args.service or args.all_services):
        config = build_config_from_cli(args)
    else:
        raise SystemExit(
            "Usage: ecs_doctor.py --cluster CLUSTER --service SERVICE\n"
            "   or: ecs_doctor.py --cluster CLUSTER --all-services\n"
            "   or: ecs_doctor.py --config config.json\n"
            "Run with --help for all options."
        )

    if config.pop("_cli_all_services", False):
        session = create_session(config)
        ecs_client = session.client("ecs")
        cluster_name = config["clusters"][0]["name"]
        service_names = list_services_in_cluster(ecs_client, cluster_name)
        if not service_names:
            raise SystemExit(f"No services found in cluster '{cluster_name}'")
        config["clusters"][0]["services"] = [{"name": name} for name in service_names]
        config.pop("_cli_service_names", None)
    elif service_names := config.pop("_cli_service_names", None):
        config["clusters"][0]["services"] = [{"name": name} for name in service_names]

    if not config.get("clusters") or not any(
        cluster.get("services") for cluster in config["clusters"]
    ):
        raise SystemExit("No services to check. Add --service NAME or use --all-services.")

    return config


def get_account_id(session: boto3.Session) -> str:
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    return identity["Account"]


def validate_account(
    session: boto3.Session, expected_account_id: Optional[str]
) -> Dict[str, Any]:
    actual_account_id = get_account_id(session)

    if expected_account_id and actual_account_id != expected_account_id:
        return {
            "status": STATUS_FAIL,
            "message": (
                f"Connected to AWS account {actual_account_id}, "
                f"but expected {expected_account_id}"
            ),
            "actual_account_id": actual_account_id,
            "expected_account_id": expected_account_id,
        }

    return {
        "status": STATUS_PASS,
        "message": f"Connected to AWS account {actual_account_id}",
        "actual_account_id": actual_account_id,
        "expected_account_id": expected_account_id,
    }


def get_primary_deployment(service: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for deployment in service.get("deployments", []):
        if deployment.get("status") == "PRIMARY":
            return deployment
    return None


def is_rollout_in_progress(service: Dict[str, Any]) -> bool:
    primary = get_primary_deployment(service)
    if not primary:
        return False
    rollout_state = primary.get("rolloutState")
    return bool(rollout_state and rollout_state != "COMPLETED")


def describe_ecs_services_batch(
    ecs_client,
    cluster_name: str,
    service_names: List[str],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """Fetch services in batches of 10 (ECS API limit)."""
    services: Dict[str, Dict[str, Any]] = {}
    errors: Dict[str, str] = {}

    for offset in range(0, len(service_names), ECS_DESCRIBE_BATCH_SIZE):
        batch = service_names[offset : offset + ECS_DESCRIBE_BATCH_SIZE]
        response = ecs_client.describe_services(
            cluster=cluster_name,
            services=batch,
            include=["TAGS"],
        )

        for failure in response.get("failures", []):
            arn = failure.get("arn", "")
            name = arn.rsplit("/", 1)[-1] if arn else "unknown"
            errors[name] = failure.get("reason", "Unknown reason")

        for service in response.get("services", []):
            services[service["serviceName"]] = service

    for name in service_names:
        if name not in services and name not in errors:
            errors[name] = "Service not found"

    return services, errors


def describe_task_definition(ecs_client, task_definition_arn: str) -> Dict[str, Any]:
    response = ecs_client.describe_task_definition(taskDefinition=task_definition_arn)
    return response["taskDefinition"]


def get_container_images(task_definition: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {
            "container": container.get("name", "unknown"),
            "image": container.get("image", "unknown"),
        }
        for container in task_definition.get("containerDefinitions", [])
    ]


def get_target_group_health(elbv2_client, target_group_arn: str) -> Dict[str, Any]:
    response = elbv2_client.describe_target_health(TargetGroupArn=target_group_arn)

    counts = {state: 0 for state in TARGET_HEALTH_COUNTS}
    targets = []

    for item in response.get("TargetHealthDescriptions", []):
        target = item.get("Target", {})
        health = item.get("TargetHealth", {})
        state = health.get("State", "unknown")
        counts[state] = counts.get(state, 0) + 1

        targets.append(
            {
                "id": target.get("Id"),
                "port": target.get("Port"),
                "state": state,
                "reason": health.get("Reason"),
                "description": health.get("Description"),
            }
        )

    return {
        "target_group_arn": target_group_arn,
        "counts": counts,
        "targets": targets,
    }


def evaluate_service_counts(
    service: Dict[str, Any],
    expected_desired_count: Optional[int],
) -> Dict[str, Any]:
    desired = service.get("desiredCount", 0)
    running = service.get("runningCount", 0)
    pending = service.get("pendingCount", 0)

    problems = []

    if expected_desired_count is not None and desired != expected_desired_count:
        problems.append(
            f"Desired count is {desired}, expected {expected_desired_count}"
        )

    if running < desired:
        problems.append(
            f"Running count is below desired count: running={running}, desired={desired}"
        )

    if pending > 0:
        problems.append(f"Service has pending tasks: pending={pending}")

    base = {
        "desired": desired,
        "running": running,
        "pending": pending,
        "expected_desired_count": expected_desired_count,
    }

    if problems:
        return {
            **base,
            "status": STATUS_FAIL,
            "message": "; ".join(problems),
        }

    return {
        **base,
        "status": STATUS_PASS,
        "message": (
            f"Task counts look stable: desired={desired}, "
            f"running={running}, pending={pending}"
        ),
    }


def evaluate_deployments(
    service: Dict[str, Any],
    warn_on_multiple_active_deployments: bool = True,
) -> Dict[str, Any]:
    deployments = service.get("deployments", [])

    active_deployments = [
        deployment
        for deployment in deployments
        if deployment.get("status") in {"PRIMARY", "ACTIVE"}
    ]

    primary = get_primary_deployment(service)
    problems = []
    warnings = []

    if not primary:
        problems.append("No PRIMARY deployment found")
    else:
        rollout_state = primary.get("rolloutState")
        if rollout_state and rollout_state != "COMPLETED":
            problems.append(f"Primary deployment rollout state is {rollout_state}")

        failed_tasks = primary.get("failedTasks", 0)
        if failed_tasks > 0:
            problems.append(f"Primary deployment has failed tasks: {failed_tasks}")

    if len(active_deployments) > 1 and warn_on_multiple_active_deployments:
        warnings.append(
            f"Multiple active deployments detected: {len(active_deployments)}"
        )

    simplified_deployments = [
        {
            "id": deployment.get("id"),
            "status": deployment.get("status"),
            "rollout_state": deployment.get("rolloutState"),
            "desired": deployment.get("desiredCount"),
            "running": deployment.get("runningCount"),
            "pending": deployment.get("pendingCount"),
            "failed_tasks": deployment.get("failedTasks"),
            "created_at": str(deployment.get("createdAt")),
            "updated_at": str(deployment.get("updatedAt")),
        }
        for deployment in deployments
    ]

    if problems:
        status = STATUS_FAIL
        message = "; ".join(problems)
    elif warnings:
        status = STATUS_WARN
        message = "; ".join(warnings)
    else:
        status = STATUS_PASS
        message = "Deployment state looks stable"

    return {
        "status": status,
        "message": message,
        "deployments": simplified_deployments,
    }


def extract_recent_events(service: Dict[str, Any], limit: int) -> List[Dict[str, str]]:
    return [
        {
            "created_at": str(event.get("createdAt")),
            "message": event.get("message", ""),
        }
        for event in service.get("events", [])[:limit]
    ]


def fetch_target_group_health_parallel(
    elbv2_client,
    target_group_arns: List[str],
    max_workers: int,
) -> List[Dict[str, Any]]:
    if not target_group_arns:
        return []

    workers = min(max_workers, len(target_group_arns))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(
            executor.map(
                lambda arn: get_target_group_health(elbv2_client, arn),
                target_group_arns,
            )
        )


def evaluate_target_health(
    service: Dict[str, Any],
    elbv2_client,
    fail_on_unhealthy_targets: bool,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> Dict[str, Any]:
    target_group_arns = [
        load_balancer["targetGroupArn"]
        for load_balancer in service.get("loadBalancers", [])
        if load_balancer.get("targetGroupArn")
    ]

    if not target_group_arns:
        return {
            "status": STATUS_WARN,
            "message": "No load balancer attached to service",
            "target_groups": [],
        }

    target_group_results = fetch_target_group_health_parallel(
        elbv2_client,
        target_group_arns,
        max_workers,
    )

    healthy_total = sum(r["counts"].get("healthy", 0) for r in target_group_results)
    unhealthy_total = sum(r["counts"].get("unhealthy", 0) for r in target_group_results)
    initial_total = sum(r["counts"].get("initial", 0) for r in target_group_results)
    rollout_active = is_rollout_in_progress(service)

    base = {
        "healthy_targets": healthy_total,
        "unhealthy_targets": unhealthy_total,
        "initial_targets": initial_total,
        "target_groups": target_group_results,
    }

    if unhealthy_total > 0 and fail_on_unhealthy_targets:
        return {
            **base,
            "status": STATUS_FAIL,
            "message": f"Unhealthy targets detected: {unhealthy_total}",
        }

    if rollout_active and initial_total > 0 and healthy_total == 0 and unhealthy_total == 0:
        return {
            **base,
            "status": STATUS_WARN,
            "message": (
                f"Targets still registering during deployment: initial={initial_total}"
            ),
        }

    if healthy_total == 0:
        return {
            **base,
            "status": STATUS_WARN,
            "message": "No healthy targets found",
        }

    parts = [f"healthy={healthy_total}", f"unhealthy={unhealthy_total}"]
    if initial_total > 0:
        parts.append(f"initial={initial_total}")

    return {
        **base,
        "status": STATUS_PASS,
        "message": f"Target group health looks good: {', '.join(parts)}",
    }


def aggregate_check_status(checks: Dict[str, Any]) -> str:
    statuses = [
        check.get("status")
        for check in checks.values()
        if isinstance(check, dict) and "status" in check
    ]

    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if STATUS_WARN in statuses:
        return STATUS_WARN
    return STATUS_PASS


def inspect_service(
    ecs_client,
    elbv2_client,
    cluster_name: str,
    service_config: Dict[str, Any],
    checks_config: Dict[str, Any],
    prefetched_service: Optional[Dict[str, Any]] = None,
    prefetch_error: Optional[str] = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> Dict[str, Any]:
    service_name = service_config["name"]
    expected_desired_count = service_config.get("expected_desired_count")

    result: Dict[str, Any] = {
        "cluster": cluster_name,
        "service": service_name,
        "critical": service_config.get("critical", False),
        "status": STATUS_PASS,
        "checks": {},
    }

    try:
        if prefetch_error:
            raise RuntimeError(
                f"Could not describe service {service_name}: {prefetch_error}"
            )

        service = prefetched_service
        if service is None:
            services, errors = describe_ecs_services_batch(
                ecs_client, cluster_name, [service_name]
            )
            if service_name in errors:
                raise RuntimeError(
                    f"Could not describe service {service_name}: {errors[service_name]}"
                )
            service = services[service_name]

        result["service_arn"] = service.get("serviceArn")
        result["task_definition"] = service.get("taskDefinition")
        result["launch_type"] = service.get("launchType")
        result["platform_version"] = service.get("platformVersion")

        result["checks"]["task_counts"] = evaluate_service_counts(
            service, expected_desired_count
        )
        result["checks"]["deployments"] = evaluate_deployments(
            service,
            checks_config.get("warn_on_multiple_active_deployments", True),
        )

        if checks_config.get("include_recent_events", True):
            limit = checks_config.get("recent_event_limit", 8)
            result["checks"]["recent_events"] = {
                "status": STATUS_PASS,
                "events": extract_recent_events(service, limit),
            }

        if checks_config.get("include_task_definition", True):
            task_definition = describe_task_definition(
                ecs_client, service["taskDefinition"]
            )
            result["checks"]["task_definition"] = {
                "status": STATUS_PASS,
                "family": task_definition.get("family"),
                "revision": task_definition.get("revision"),
                "cpu": task_definition.get("cpu"),
                "memory": task_definition.get("memory"),
                "network_mode": task_definition.get("networkMode"),
                "requires_compatibilities": task_definition.get(
                    "requiresCompatibilities"
                ),
                "container_images": get_container_images(task_definition),
            }

        if checks_config.get("include_target_group_health", True):
            result["checks"]["target_group_health"] = evaluate_target_health(
                service,
                elbv2_client,
                checks_config.get("fail_on_unhealthy_targets", True),
                max_workers,
            )

        result["status"] = aggregate_check_status(result["checks"])

    except Exception as exc:
        result["status"] = STATUS_FAIL
        result["error"] = str(exc)

    return result


def update_summary(summary: Dict[str, int], service_result: Dict[str, Any]) -> None:
    summary["total_services"] += 1

    status = service_result["status"]
    if status == STATUS_PASS:
        summary["passed"] += 1
    elif status == STATUS_WARN:
        summary["warnings"] += 1
    else:
        summary["failed"] += 1
        if service_result.get("critical"):
            summary["critical_failed"] += 1


def inspect_all(config: Dict[str, Any]) -> Dict[str, Any]:
    session = create_session(config)
    ecs_client = session.client("ecs")
    elbv2_client = session.client("elbv2")

    aws_config = config.get("aws", {})
    checks_config = config.get("checks", {})
    max_workers = aws_config.get("max_workers", DEFAULT_MAX_WORKERS)

    report: Dict[str, Any] = {
        "tool": "ecs-service-doctor",
        "version": VERSION,
        "generated_at": utc_now(),
        "region": aws_config.get("region", "us-east-1"),
        "account_check": {},
        "summary": {
            "total_services": 0,
            "passed": 0,
            "warnings": 0,
            "failed": 0,
            "critical_failed": 0,
        },
        "results": [],
    }

    account_check = validate_account(session, aws_config.get("expected_account_id"))
    report["account_check"] = account_check

    if account_check["status"] == STATUS_FAIL:
        report["summary"]["failed"] += 1
        report["summary"]["critical_failed"] += 1
        return report

    work_items: List[Tuple[str, Dict[str, Any], Optional[Dict[str, Any]], Optional[str]]] = []

    for cluster in config.get("clusters", []):
        cluster_name = cluster["name"]
        service_configs = cluster.get("services", [])
        service_names = [service_config["name"] for service_config in service_configs]

        if not service_names:
            continue

        services_map, errors_map = describe_ecs_services_batch(
            ecs_client, cluster_name, service_names
        )

        for service_config in service_configs:
            name = service_config["name"]
            work_items.append(
                (
                    cluster_name,
                    service_config,
                    services_map.get(name),
                    errors_map.get(name),
                )
            )

    if not work_items:
        return report

    workers = min(max_workers, len(work_items))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                inspect_service,
                ecs_client,
                elbv2_client,
                cluster_name,
                service_config,
                checks_config,
                prefetched_service,
                prefetch_error,
                max_workers,
            )
            for cluster_name, service_config, prefetched_service, prefetch_error in work_items
        ]

        for future in futures:
            service_result = future.result()
            report["results"].append(service_result)
            update_summary(report["summary"], service_result)

    return report


def compute_exit_code(report: Dict[str, Any]) -> int:
    if report["account_check"]["status"] == STATUS_FAIL:
        return 2

    summary = report["summary"]
    if summary.get("critical_failed", 0) > 0:
        return 2

    if summary["failed"] > 0 or summary["warnings"] > 0:
        return 1

    return 0


def summarize_service_plain(item: Dict[str, Any]) -> List[str]:
    lines: List[str] = []

    if item.get("error"):
        lines.append(f"  Could not check service: {item['error']}")
        return lines

    checks = item.get("checks", {})

    task_counts = checks.get("task_counts", {})
    desired = task_counts.get("desired", "?")
    running = task_counts.get("running", "?")
    if task_counts.get("status") == STATUS_PASS:
        lines.append(f"  Tasks: {running}/{desired} running")
    else:
        lines.append(f"  Tasks: {running}/{desired} running — {task_counts.get('message', '')}")

    deployments = checks.get("deployments", {})
    rollout = deployments.get("status")
    if rollout == STATUS_PASS:
        lines.append("  Deployment: finished")
    elif rollout == STATUS_WARN:
        lines.append(f"  Deployment: {deployments.get('message', 'needs attention')}")
    else:
        lines.append(f"  Deployment: {deployments.get('message', 'problem detected')}")

    target_health = checks.get("target_group_health")
    if target_health:
        if target_health.get("status") == STATUS_PASS:
            lines.append(f"  Load balancer: {target_health.get('message', 'OK')}")
        else:
            lines.append(f"  Load balancer: {target_health.get('message', 'issue detected')}")

    images = checks.get("task_definition", {}).get("container_images", [])
    if images:
        primary = images[0]
        lines.append(f"  Image: {primary['image']}")

    if item.get("status") != STATUS_PASS:
        events = checks.get("recent_events", {}).get("events", [])
        if events:
            lines.append(f"  Latest event: {events[0]['message']}")

    return lines


def print_simple_report(report: Dict[str, Any]) -> None:
    status_word = {
        STATUS_PASS: "HEALTHY",
        STATUS_WARN: "WARNING",
        STATUS_FAIL: "UNHEALTHY",
    }

    print()
    print("ECS Service Check")
    print("=" * 50)
    print(f"Region: {report['region']}")
    print(f"Account: {report['account_check'].get('actual_account_id', 'unknown')}")
    print()

    if report["account_check"]["status"] == STATUS_FAIL:
        print(f"STOP — {report['account_check']['message']}")
        print()
        return

    for item in report["results"]:
        word = status_word.get(item["status"], item["status"])
        print(f"[{word}] {item['cluster']} / {item['service']}")
        for line in summarize_service_plain(item):
            print(line)
        print()

    summary = report["summary"]
    total = summary["total_services"]
    passed = summary["passed"]
    print("=" * 50)
    if summary["failed"]:
        print(f"Result: {passed}/{total} services healthy — problems found")
    elif summary["warnings"]:
        print(f"Result: {passed}/{total} services healthy — review warnings above")
    else:
        print(f"Result: all {total} service(s) look healthy")
    print()


def print_human_report(report: Dict[str, Any]) -> None:
    print()
    print("ECS SERVICE DOCTOR")
    print("=" * 70)
    print(f"Generated At : {report['generated_at']}")
    print(f"Region       : {report['region']}")
    print()

    account_check = report["account_check"]
    print(f"AWS Account  : [{account_check['status']}] {account_check['message']}")
    print()

    summary = report["summary"]
    print("Summary")
    print("-" * 70)
    print(f"Total Services : {summary['total_services']}")
    print(f"Passed         : {summary['passed']}")
    print(f"Warnings       : {summary['warnings']}")
    print(f"Failed         : {summary['failed']}")
    if summary.get("critical_failed"):
        print(f"Critical Fail  : {summary['critical_failed']}")
    print()

    for item in report["results"]:
        print("-" * 70)
        critical_tag = " [critical]" if item.get("critical") else ""
        print(f"[{item['status']}] {item['cluster']} / {item['service']}{critical_tag}")

        if item.get("error"):
            print(f"  Error: {item['error']}")
            continue

        print(f"  Task Definition : {item.get('task_definition')}")
        print(f"  Launch Type     : {item.get('launch_type')}")
        print(f"  Platform        : {item.get('platform_version')}")

        checks = item.get("checks", {})

        task_counts = checks.get("task_counts")
        if task_counts:
            print(f"  Task Counts     : [{task_counts['status']}] {task_counts['message']}")

        deployments = checks.get("deployments")
        if deployments:
            print(f"  Deployments     : [{deployments['status']}] {deployments['message']}")

        target_health = checks.get("target_group_health")
        if target_health:
            print(f"  Target Health   : [{target_health['status']}] {target_health['message']}")

        task_definition = checks.get("task_definition")
        if task_definition:
            for image in task_definition.get("container_images", []):
                print(f"  Image           : {image['container']} -> {image['image']}")

        recent_events = checks.get("recent_events", {}).get("events", [])
        if recent_events:
            print("  Recent Events:")
            for event in recent_events[:5]:
                print(f"    - {event['message']}")

    print("-" * 70)
    print()


def esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def status_css(status: str) -> str:
    return {"PASS": "pass", "WARN": "warn", "FAIL": "fail"}.get(status, "unknown")


def status_label(status: str) -> str:
    return {
        STATUS_PASS: "Healthy",
        STATUS_WARN: "Warning",
        STATUS_FAIL: "Unhealthy",
    }.get(status, status)


def group_results_by_cluster(results: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in results:
        grouped.setdefault(item["cluster"], []).append(item)
    return grouped


def render_check_rows(item: Dict[str, Any]) -> str:
    if item.get("error"):
        return f'<tr><td colspan="2" class="error">{esc(item["error"])}</td></tr>'

    checks = item.get("checks", {})
    rows = []

    meta = [
        ("Task definition", item.get("task_definition")),
        ("Launch type", item.get("launch_type")),
        ("Platform", item.get("platform_version")),
    ]
    for label, value in meta:
        if value:
            rows.append(f"<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>")

    for check_name, check in checks.items():
        if not isinstance(check, dict):
            continue
        if check_name == "recent_events":
            continue
        if "status" in check and "message" in check:
            badge = f'<span class="badge {status_css(check["status"])}">{esc(check["status"])}</span>'
            rows.append(
                f"<tr><th>{esc(check_name.replace('_', ' ').title())}</th>"
                f"<td>{badge} {esc(check['message'])}</td></tr>"
            )
        elif check_name == "task_definition":
            for image in check.get("container_images", []):
                rows.append(
                    f"<tr><th>Container</th>"
                    f"<td>{esc(image['container'])} → {esc(image['image'])}</td></tr>"
                )

    return "\n".join(rows)


def render_events_block(item: Dict[str, Any]) -> str:
    events = item.get("checks", {}).get("recent_events", {}).get("events", [])
    if not events:
        return ""

    items = "".join(f"<li>{esc(event.get('message', ''))}</li>" for event in events[:8])
    return f"""
    <div class="events">
      <h4>Recent events</h4>
      <ul>{items}</ul>
    </div>
    """


def render_service_card(item: Dict[str, Any]) -> str:
    critical = ' <span class="critical">critical</span>' if item.get("critical") else ""
    return f"""
    <article class="service-card {status_css(item['status'])}">
      <header>
        <h3>{esc(item['service'])}{critical}</h3>
        <span class="badge {status_css(item['status'])}">{esc(status_label(item['status']))}</span>
      </header>
      <table class="checks">
        <tbody>
          {render_check_rows(item)}
        </tbody>
      </table>
      {render_events_block(item)}
    </article>
    """


def render_html_report(report: Dict[str, Any]) -> str:
    summary = report["summary"]
    account = report["account_check"]
    grouped = group_results_by_cluster(report.get("results", []))

    overall = STATUS_PASS
    if summary.get("failed"):
        overall = STATUS_FAIL
    elif summary.get("warnings"):
        overall = STATUS_WARN

    cluster_sections = []
    for cluster_name, services in grouped.items():
        cards = "".join(render_service_card(item) for item in services)
        cluster_sections.append(
            f"""
            <section class="cluster">
              <h2>{esc(cluster_name)}</h2>
              <p class="cluster-meta">{len(services)} service(s)</p>
              <div class="services">{cards}</div>
            </section>
            """
        )

    account_row = ""
    if account.get("status") == STATUS_FAIL:
        account_row = f'<p class="account-error">{esc(account["message"])}</p>'
    else:
        account_row = f"<p><strong>Account:</strong> {esc(account.get('actual_account_id', 'unknown'))}</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ECS Service Doctor — {esc(report.get('generated_at', ''))}</title>
  <style>
    :root {{
      --bg: #f4f6f8;
      --card: #ffffff;
      --text: #1a1a2e;
      --muted: #5c6370;
      --border: #dde2e8;
      --pass: #0f7b4a;
      --pass-bg: #e6f4ed;
      --warn: #9a6700;
      --warn-bg: #fff8e6;
      --fail: #b42318;
      --fail-bg: #fdecea;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    .wrap {{ max-width: 960px; margin: 0 auto; padding: 2rem 1.25rem 3rem; }}
    header.page {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
    }}
    header.page h1 {{ margin: 0 0 0.25rem; font-size: 1.5rem; }}
    header.page .meta {{ color: var(--muted); margin: 0.25rem 0; }}
    .overall {{
      display: inline-block;
      margin-top: 0.75rem;
      padding: 0.35rem 0.75rem;
      border-radius: 999px;
      font-weight: 600;
      font-size: 0.9rem;
    }}
    .overall.pass {{ background: var(--pass-bg); color: var(--pass); }}
    .overall.warn {{ background: var(--warn-bg); color: var(--warn); }}
    .overall.fail {{ background: var(--fail-bg); color: var(--fail); }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 0.75rem;
      margin-bottom: 1.5rem;
    }}
    .stat {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1rem;
      text-align: center;
    }}
    .stat .num {{ font-size: 1.75rem; font-weight: 700; }}
    .stat .lbl {{ color: var(--muted); font-size: 0.85rem; }}
    .cluster {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.25rem;
      margin-bottom: 1.25rem;
    }}
    .cluster h2 {{ margin: 0; font-size: 1.15rem; }}
    .cluster-meta {{ color: var(--muted); margin: 0.25rem 0 1rem; font-size: 0.9rem; }}
    .services {{ display: grid; gap: 1rem; }}
    .service-card {{
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1rem;
      border-left-width: 4px;
    }}
    .service-card.pass {{ border-left-color: var(--pass); }}
    .service-card.warn {{ border-left-color: var(--warn); }}
    .service-card.fail {{ border-left-color: var(--fail); }}
    .service-card header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 0.75rem;
    }}
    .service-card h3 {{ margin: 0; font-size: 1rem; }}
    .badge {{
      display: inline-block;
      padding: 0.15rem 0.5rem;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
      white-space: nowrap;
    }}
    .badge.pass {{ background: var(--pass-bg); color: var(--pass); }}
    .badge.warn {{ background: var(--warn-bg); color: var(--warn); }}
    .badge.fail {{ background: var(--fail-bg); color: var(--fail); }}
    .critical {{
      font-size: 0.7rem;
      text-transform: uppercase;
      color: var(--fail);
      margin-left: 0.35rem;
    }}
    table.checks {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
    }}
    table.checks th {{
      text-align: left;
      width: 140px;
      padding: 0.35rem 0.75rem 0.35rem 0;
      color: var(--muted);
      font-weight: 500;
      vertical-align: top;
    }}
    table.checks td {{ padding: 0.35rem 0; vertical-align: top; }}
    .events {{ margin-top: 0.75rem; }}
    .events h4 {{ margin: 0 0 0.35rem; font-size: 0.85rem; color: var(--muted); }}
    .events ul {{ margin: 0; padding-left: 1.25rem; font-size: 0.85rem; }}
    .events li {{ margin-bottom: 0.25rem; }}
    .account-error {{ color: var(--fail); font-weight: 600; }}
    footer {{ text-align: center; color: var(--muted); font-size: 0.8rem; margin-top: 1.5rem; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="page">
      <h1>ECS Service Doctor</h1>
      <p class="meta">Generated {esc(report.get('generated_at'))} · Region {esc(report.get('region'))}</p>
      {account_row}
      <span class="overall {status_css(overall)}">{esc(status_label(overall))}</span>
    </header>

    <div class="summary">
      <div class="stat"><div class="num">{summary.get('total_services', 0)}</div><div class="lbl">Services</div></div>
      <div class="stat"><div class="num">{summary.get('passed', 0)}</div><div class="lbl">Healthy</div></div>
      <div class="stat"><div class="num">{summary.get('warnings', 0)}</div><div class="lbl">Warnings</div></div>
      <div class="stat"><div class="num">{summary.get('failed', 0)}</div><div class="lbl">Failed</div></div>
    </div>

    {"".join(cluster_sections) if cluster_sections else "<p>No services were checked.</p>"}

    <footer>ecs-service-doctor v{esc(report.get('version', VERSION))}</footer>
  </div>
</body>
</html>
"""


def write_html_report(report: Dict[str, Any], path: str) -> None:
    content = render_html_report(report)
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check whether ECS services are truly healthy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quick start (no config file):
  %(prog)s --cluster my-cluster --service my-api
  %(prog)s --cluster my-cluster --service api --service worker
  %(prog)s --cluster my-cluster --all-services

With a config file:
  %(prog)s --config config.json
        """.strip(),
    )

    parser.add_argument(
        "-c",
        "--cluster",
        help="ECS cluster name",
    )

    parser.add_argument(
        "-s",
        "--service",
        action="append",
        metavar="NAME",
        help="ECS service name (repeat for multiple services)",
    )

    parser.add_argument(
        "--all-services",
        action="store_true",
        help="Check every service in the cluster",
    )

    parser.add_argument(
        "--config",
        help="Optional JSON config file (for many clusters or advanced options)",
    )

    parser.add_argument(
        "--region",
        help="AWS region (defaults to your AWS CLI / environment setting)",
    )

    parser.add_argument(
        "--profile",
        help="AWS CLI profile name",
    )

    parser.add_argument(
        "--account",
        metavar="ID",
        help="Expected AWS account ID (safety check for prod)",
    )

    parser.add_argument(
        "--html",
        nargs="?",
        const="ecs_report.html",
        metavar="FILE",
        help="Write HTML report (default: ecs_report.html)",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON (for CI/CD)",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed technical output",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"ecs-service-doctor {VERSION}",
    )

    args = parser.parse_args()

    try:
        config = resolve_input_config(args)
        report = inspect_all(config)

        if args.html:
            write_html_report(report, args.html)
            print(f"HTML report saved to {args.html}")

        if args.json:
            print(json.dumps(report, indent=2, default=str))
        elif args.verbose:
            print_human_report(report)
        else:
            print_simple_report(report)

        return compute_exit_code(report)

    except ClientError as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
