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
    python ecs_doctor.py --config config.example.json
    python ecs_doctor.py --config config.example.json --json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError, ProfileNotFound


STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def create_session(config: Dict[str, Any]) -> boto3.Session:
    aws_config = config.get("aws", {})
    profile = aws_config.get("profile")
    region = aws_config.get("region", "us-east-1")

    try:
        if profile:
            return boto3.Session(profile_name=profile, region_name=region)
        return boto3.Session(region_name=region)
    except ProfileNotFound as exc:
        raise SystemExit(f"ERROR: AWS profile not found: {profile}") from exc


def get_account_id(session: boto3.Session) -> str:
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    return identity["Account"]


def validate_account(session: boto3.Session, expected_account_id: Optional[str]) -> Dict[str, Any]:
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


def describe_ecs_service(ecs_client, cluster_name: str, service_name: str) -> Dict[str, Any]:
    response = ecs_client.describe_services(
        cluster=cluster_name,
        services=[service_name],
        include=["TAGS"]
    )

    failures = response.get("failures", [])
    if failures:
        reason = failures[0].get("reason", "Unknown reason")
        raise RuntimeError(f"Could not describe service {service_name}: {reason}")

    services = response.get("services", [])
    if not services:
        raise RuntimeError(f"Service not found: {service_name}")

    return services[0]


def describe_task_definition(ecs_client, task_definition_arn: str) -> Dict[str, Any]:
    response = ecs_client.describe_task_definition(taskDefinition=task_definition_arn)
    return response["taskDefinition"]


def get_container_images(task_definition: Dict[str, Any]) -> List[Dict[str, str]]:
    images = []

    for container in task_definition.get("containerDefinitions", []):
        images.append(
            {
                "container": container.get("name", "unknown"),
                "image": container.get("image", "unknown"),
            }
        )

    return images


def get_target_group_health(elbv2_client, target_group_arn: str) -> Dict[str, Any]:
    response = elbv2_client.describe_target_health(TargetGroupArn=target_group_arn)

    targets = []
    counts = {
        "healthy": 0,
        "unhealthy": 0,
        "initial": 0,
        "draining": 0,
        "unused": 0,
        "unavailable": 0,
        "unknown": 0,
    }

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
    expected_desired_count: Optional[int]
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

    if problems:
        return {
            "status": STATUS_FAIL,
            "message": "; ".join(problems),
            "desired": desired,
            "running": running,
            "pending": pending,
            "expected_desired_count": expected_desired_count,
        }

    return {
        "status": STATUS_PASS,
        "message": f"Task counts look stable: desired={desired}, running={running}, pending={pending}",
        "desired": desired,
        "running": running,
        "pending": pending,
        "expected_desired_count": expected_desired_count,
    }


def evaluate_deployments(service: Dict[str, Any]) -> Dict[str, Any]:
    deployments = service.get("deployments", [])

    active_deployments = [
        deployment for deployment in deployments
        if deployment.get("status") in {"PRIMARY", "ACTIVE"}
    ]

    primary = next(
        (
            deployment for deployment in deployments
            if deployment.get("status") == "PRIMARY"
        ),
        None
    )

    problems = []
    warnings = []

    if not primary:
        problems.append("No PRIMARY deployment found")

    if primary:
        rollout_state = primary.get("rolloutState")

        if rollout_state and rollout_state != "COMPLETED":
            problems.append(f"Primary deployment rollout state is {rollout_state}")

        failed_tasks = primary.get("failedTasks", 0)
        if failed_tasks > 0:
            problems.append(f"Primary deployment has failed tasks: {failed_tasks}")

    if len(active_deployments) > 1:
        warnings.append(
            f"Multiple active deployments detected: {len(active_deployments)}"
        )

    simplified_deployments = []

    for deployment in deployments:
        simplified_deployments.append(
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
        )

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
    events = service.get("events", [])[:limit]

    return [
        {
            "created_at": str(event.get("createdAt")),
            "message": event.get("message", ""),
        }
        for event in events
    ]


def evaluate_target_health(
    service: Dict[str, Any],
    elbv2_client,
    fail_on_unhealthy_targets: bool
) -> Dict[str, Any]:
    load_balancers = service.get("loadBalancers", [])

    if not load_balancers:
        return {
            "status": STATUS_WARN,
            "message": "No load balancer attached to service",
            "target_groups": [],
        }

    target_group_results = []
    unhealthy_total = 0
    healthy_total = 0

    for load_balancer in load_balancers:
        target_group_arn = load_balancer.get("targetGroupArn")

        if not target_group_arn:
            continue

        health = get_target_group_health(elbv2_client, target_group_arn)
        target_group_results.append(health)

        counts = health["counts"]
        healthy_total += counts.get("healthy", 0)
        unhealthy_total += counts.get("unhealthy", 0)

    if unhealthy_total > 0 and fail_on_unhealthy_targets:
        return {
            "status": STATUS_FAIL,
            "message": f"Unhealthy targets detected: {unhealthy_total}",
            "healthy_targets": healthy_total,
            "unhealthy_targets": unhealthy_total,
            "target_groups": target_group_results,
        }

    if healthy_total == 0:
        return {
            "status": STATUS_WARN,
            "message": "No healthy targets found",
            "healthy_targets": healthy_total,
            "unhealthy_targets": unhealthy_total,
            "target_groups": target_group_results,
        }

    return {
        "status": STATUS_PASS,
        "message": f"Target group health looks good: healthy={healthy_total}, unhealthy={unhealthy_total}",
        "healthy_targets": healthy_total,
        "unhealthy_targets": unhealthy_total,
        "target_groups": target_group_results,
    }


def inspect_service(
    ecs_client,
    elbv2_client,
    cluster_name: str,
    service_config: Dict[str, Any],
    checks_config: Dict[str, Any]
) -> Dict[str, Any]:
    service_name = service_config["name"]
    expected_desired_count = service_config.get("expected_desired_count")

    result = {
        "cluster": cluster_name,
        "service": service_name,
        "critical": service_config.get("critical", False),
        "status": STATUS_PASS,
        "checks": {},
    }

    try:
        service = describe_ecs_service(ecs_client, cluster_name, service_name)

        result["service_arn"] = service.get("serviceArn")
        result["task_definition"] = service.get("taskDefinition")
        result["launch_type"] = service.get("launchType")
        result["platform_version"] = service.get("platformVersion")

        count_check = evaluate_service_counts(service, expected_desired_count)
        deployment_check = evaluate_deployments(service)

        result["checks"]["task_counts"] = count_check
        result["checks"]["deployments"] = deployment_check

        if checks_config.get("include_recent_events", True):
            limit = checks_config.get("recent_event_limit", 8)
            result["checks"]["recent_events"] = {
                "status": STATUS_PASS,
                "events": extract_recent_events(service, limit),
            }

        if checks_config.get("include_task_definition", True):
            task_definition = describe_task_definition(
                ecs_client,
                service["taskDefinition"]
            )
            result["checks"]["task_definition"] = {
                "status": STATUS_PASS,
                "family": task_definition.get("family"),
                "revision": task_definition.get("revision"),
                "cpu": task_definition.get("cpu"),
                "memory": task_definition.get("memory"),
                "network_mode": task_definition.get("networkMode"),
                "requires_compatibilities": task_definition.get("requiresCompatibilities"),
                "container_images": get_container_images(task_definition),
            }

        if checks_config.get("include_target_group_health", True):
            target_health_check = evaluate_target_health(
                service,
                elbv2_client,
                checks_config.get("fail_on_unhealthy_targets", True)
            )
            result["checks"]["target_group_health"] = target_health_check

        check_statuses = [
            check.get("status")
            for check in result["checks"].values()
            if isinstance(check, dict) and "status" in check
        ]

        if STATUS_FAIL in check_statuses:
            result["status"] = STATUS_FAIL
        elif STATUS_WARN in check_statuses:
            result["status"] = STATUS_WARN
        else:
            result["status"] = STATUS_PASS

    except Exception as exc:
        result["status"] = STATUS_FAIL
        result["error"] = str(exc)

    return result


def inspect_all(config: Dict[str, Any]) -> Dict[str, Any]:
    session = create_session(config)
    ecs_client = session.client("ecs")
    elbv2_client = session.client("elbv2")

    aws_config = config.get("aws", {})
    checks_config = config.get("checks", {})

    report = {
        "tool": "ecs-service-doctor",
        "generated_at": utc_now(),
        "region": aws_config.get("region", "us-east-1"),
        "account_check": {},
        "summary": {
            "total_services": 0,
            "passed": 0,
            "warnings": 0,
            "failed": 0,
        },
        "results": [],
    }

    account_check = validate_account(
        session,
        aws_config.get("expected_account_id")
    )

    report["account_check"] = account_check

    if account_check["status"] == STATUS_FAIL:
        report["summary"]["failed"] += 1
        return report

    for cluster in config.get("clusters", []):
        cluster_name = cluster["name"]

        for service_config in cluster.get("services", []):
            report["summary"]["total_services"] += 1

            service_result = inspect_service(
                ecs_client=ecs_client,
                elbv2_client=elbv2_client,
                cluster_name=cluster_name,
                service_config=service_config,
                checks_config=checks_config,
            )

            report["results"].append(service_result)

            if service_result["status"] == STATUS_PASS:
                report["summary"]["passed"] += 1
            elif service_result["status"] == STATUS_WARN:
                report["summary"]["warnings"] += 1
            else:
                report["summary"]["failed"] += 1

    return report


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
    print()

    for item in report["results"]:
        print("-" * 70)
        print(f"[{item['status']}] {item['cluster']} / {item['service']}")

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
            images = task_definition.get("container_images", [])
            for image in images:
                print(f"  Image           : {image['container']} -> {image['image']}")

        recent_events = checks.get("recent_events", {}).get("events", [])
        if recent_events:
            print("  Recent Events:")
            for event in recent_events[:5]:
                print(f"    - {event['message']}")

    print("-" * 70)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate ECS service health beyond basic task counts."
    )

    parser.add_argument(
        "--config",
        required=True,
        help="Path to JSON config file"
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full report as JSON"
    )

    args = parser.parse_args()

    try:
        config = load_config(args.config)
        report = inspect_all(config)

        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print_human_report(report)

        if report["summary"]["failed"] > 0:
            return 2

        if report["summary"]["warnings"] > 0:
            return 1

        return 0

    except ClientError as exc:
        print(f"AWS API error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted by user", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
