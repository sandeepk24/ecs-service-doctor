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
- recent stable task definitions for rollback
- HTTP endpoint health using ALB target-group path/matcher (and notifications)
- Slack, Microsoft Teams, webhook, and SNS alerts
- continuous monitoring with --interval

Usage:
    python ecs_doctor.py --cluster my-cluster --service my-api
    python ecs_doctor.py --cluster my-cluster --all-services
    python ecs_doctor.py --config config.json
    python ecs_doctor.py --config config.json --json
"""

import argparse
import fnmatch
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError, ProfileNotFound

from notifications import (
    dispatch_notifications,
    fingerprint_unhealthy,
    should_notify,
)
from topology import (
    build_route53_index,
    build_service_mesh,
    collect_peer_hints,
    discover_connectivity,
    empty_route53_catalog,
    route53_report_summary,
)


VERSION = "0.8.5"
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
        "notifications": dict(raw.get("notifications", {})),
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
        "notifications": {},
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


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_cpu_reserved(cpu: Any) -> Optional[str]:
    units = _as_int(cpu)
    if units is None:
        return str(cpu) if cpu else None
    vcpu = units / 1024
    if units % 256 == 0:
        if vcpu == 1:
            return "1 vCPU"
        if vcpu == int(vcpu):
            return f"{int(vcpu)} vCPU"
        return f"{vcpu:g} vCPU"
    return f"{units} CPU units"


def format_memory_reserved(memory: Any) -> Optional[str]:
    mib = _as_int(memory)
    if mib is None:
        return str(memory) if memory else None
    if mib >= 1024 and mib % 1024 == 0:
        return f"{mib // 1024} GiB"
    if mib >= 1024:
        return f"{mib / 1024:.1f} GiB"
    return f"{mib} MiB"


def extract_task_resources(
    task_definition: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str]]:
    if not task_definition:
        return None, None

    cpu = task_definition.get("cpu")
    memory = task_definition.get("memory")
    if cpu and memory:
        return str(cpu), str(memory)

    cpu_total = 0
    memory_total = 0
    found_cpu = False
    found_memory = False
    for container in task_definition.get("containerDefinitions", []):
        container_cpu = _as_int(container.get("cpu"))
        container_memory = _as_int(
            container.get("memory") or container.get("memoryReservation")
        )
        if container_cpu is not None:
            cpu_total += container_cpu
            found_cpu = True
        if container_memory is not None:
            memory_total += container_memory
            found_memory = True

    return (
        str(cpu_total) if found_cpu else (str(cpu) if cpu else None),
        str(memory_total) if found_memory else (str(memory) if memory else None),
    )


def fetch_service_utilization(
    cloudwatch_client,
    cluster_name: str,
    service_name: str,
    lookback_minutes: int = 15,
    period_seconds: int = 300,
) -> Dict[str, Optional[float]]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes)
    dimensions = [
        {"Name": "ClusterName", "Value": cluster_name},
        {"Name": "ServiceName", "Value": service_name},
    ]
    response = cloudwatch_client.get_metric_data(
        MetricDataQueries=[
            {
                "Id": "cpu",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/ECS",
                        "MetricName": "CPUUtilization",
                        "Dimensions": dimensions,
                    },
                    "Period": period_seconds,
                    "Stat": "Average",
                },
                "ReturnData": True,
            },
            {
                "Id": "memory",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/ECS",
                        "MetricName": "MemoryUtilization",
                        "Dimensions": dimensions,
                    },
                    "Period": period_seconds,
                    "Stat": "Average",
                },
                "ReturnData": True,
            },
        ],
        StartTime=start,
        EndTime=end,
        ScanBy="TimestampDescending",
    )

    values: Dict[str, Optional[float]] = {"cpu": None, "memory": None}
    for result in response.get("MetricDataResults", []):
        metric_id = result.get("Id")
        points = result.get("Values") or []
        if metric_id in values and points:
            values[metric_id] = sum(points) / len(points)
    return values


def _utilization_status(
    utilization: Optional[float],
    warn_percent: float,
    fail_percent: float,
) -> str:
    if utilization is None:
        return STATUS_PASS
    if utilization >= fail_percent:
        return STATUS_FAIL
    if utilization >= warn_percent:
        return STATUS_WARN
    return STATUS_PASS


def _resource_part(
    kind: str,
    reserved: Optional[str],
    utilization: Optional[float],
    warn_percent: float,
    fail_percent: float,
) -> Dict[str, Any]:
    reserved_label = (
        format_cpu_reserved(reserved)
        if kind == "cpu"
        else format_memory_reserved(reserved)
    )
    bits: List[str] = []
    if utilization is not None:
        bits.append(f"{utilization:.0f}% average")
    if reserved_label:
        bits.append(f"reserved {reserved_label}")
    if utilization is None and reserved_label:
        bits.append("utilization not available")
    if not bits:
        bits.append(f"{kind.upper()} data not available")

    return {
        "status": _utilization_status(utilization, warn_percent, fail_percent),
        "message": " · ".join(bits),
        "reserved": reserved,
        "reserved_label": reserved_label,
        "utilization": None if utilization is None else round(utilization, 1),
    }


def evaluate_cpu_memory(
    task_definition: Optional[Dict[str, Any]],
    utilization: Optional[Dict[str, Optional[float]]] = None,
    cpu_warn_percent: float = 80,
    cpu_fail_percent: float = 90,
    memory_warn_percent: float = 80,
    memory_fail_percent: float = 90,
    lookback_minutes: int = 15,
) -> Dict[str, Any]:
    cpu_reserved, memory_reserved = extract_task_resources(task_definition)
    metrics = utilization or {}
    cpu = _resource_part(
        "cpu",
        cpu_reserved,
        metrics.get("cpu"),
        cpu_warn_percent,
        cpu_fail_percent,
    )
    memory = _resource_part(
        "memory",
        memory_reserved,
        metrics.get("memory"),
        memory_warn_percent,
        memory_fail_percent,
    )
    statuses = [cpu["status"], memory["status"]]
    if STATUS_FAIL in statuses:
        status = STATUS_FAIL
    elif STATUS_WARN in statuses:
        status = STATUS_WARN
    else:
        status = STATUS_PASS

    return {
        "status": status,
        "message": f"CPU {cpu['message']} · Memory {memory['message']}",
        "cpu": cpu,
        "memory": memory,
        "lookback_minutes": lookback_minutes,
    }


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


TASK_DEFINITION_REF_RE = re.compile(
    r"task-definition/([A-Za-z0-9_-]+):(\d+)"
)
STEADY_STATE_MARKERS = (
    "has reached a steady state",
    "deployment completed",
)
STABLE_STOP_CODES = {
    "ServiceSchedulerInitiated",
    "UserInitiated",
}


def parse_task_definition_arn(task_definition_arn: str) -> Dict[str, Any]:
    family_revision = task_definition_arn.rsplit("/", 1)[-1]
    family, revision_str = family_revision.rsplit(":", 1)
    return {
        "family": family,
        "revision": int(revision_str),
        "task_definition": family_revision,
    }


def resolve_task_definition_arn(
    service: Dict[str, Any],
    family: str,
    revision: str,
) -> str:
    suffix = f"{family}:{revision}"
    for deployment in service.get("deployments", []):
        task_definition_arn = deployment.get("taskDefinition", "")
        if task_definition_arn.endswith(suffix):
            return task_definition_arn

    current = service.get("taskDefinition", "")
    if current.endswith(suffix):
        return current

    if current and "/" in current:
        return f"{current.rsplit('/', 1)[0]}/{suffix}"

    return suffix


def build_rollback_command(
    cluster_name: str,
    service_name: str,
    task_definition: str,
) -> str:
    return (
        f"aws ecs update-service --cluster {cluster_name} "
        f"--service {service_name} --task-definition {task_definition} "
        "--force-new-deployment"
    )


def _upsert_stable_candidate(
    candidates: Dict[str, Dict[str, Any]],
    task_definition_arn: str,
    last_stable_at: Any,
    source: str,
    **extra: Any,
) -> None:
    parsed = parse_task_definition_arn(task_definition_arn)
    timestamp = str(last_stable_at) if last_stable_at else None
    existing = candidates.get(task_definition_arn)

    if existing and existing.get("last_stable_at") and timestamp:
        if existing["last_stable_at"] >= timestamp:
            existing.update({key: value for key, value in extra.items() if value is not None})
            return

    candidates[task_definition_arn] = {
        "task_definition_arn": task_definition_arn,
        **parsed,
        "last_stable_at": timestamp,
        "source": source,
        **extra,
    }


def collect_stable_tasks_from_deployments(
    service: Dict[str, Any],
    candidates: Dict[str, Dict[str, Any]],
) -> None:
    for deployment in service.get("deployments", []):
        task_definition_arn = deployment.get("taskDefinition")
        if not task_definition_arn:
            continue

        rollout_state = deployment.get("rolloutState")
        running_count = deployment.get("runningCount", 0)
        if rollout_state != "COMPLETED" and running_count <= 0:
            continue

        _upsert_stable_candidate(
            candidates,
            task_definition_arn,
            deployment.get("updatedAt") or deployment.get("createdAt"),
            "deployment",
            deployment_status=deployment.get("status"),
            running_count=running_count,
            rollout_state=rollout_state,
        )


def collect_stable_tasks_from_events(
    service: Dict[str, Any],
    candidates: Dict[str, Dict[str, Any]],
) -> None:
    events = service.get("events", [])
    for index, event in enumerate(events):
        message = event.get("message", "")
        lowered = message.lower()
        if not any(marker in lowered for marker in STEADY_STATE_MARKERS):
            continue

        task_definition_arn = None
        match = TASK_DEFINITION_REF_RE.search(message)
        if match:
            task_definition_arn = resolve_task_definition_arn(
                service,
                match.group(1),
                match.group(2),
            )

        if not task_definition_arn:
            for older_event in events[index + 1 : index + 20]:
                match = TASK_DEFINITION_REF_RE.search(older_event.get("message", ""))
                if match:
                    task_definition_arn = resolve_task_definition_arn(
                        service,
                        match.group(1),
                        match.group(2),
                    )
                    break

        if not task_definition_arn:
            continue

        _upsert_stable_candidate(
            candidates,
            task_definition_arn,
            event.get("createdAt"),
            "steady_state_event",
        )


def collect_stable_tasks_from_stopped_tasks(
    ecs_client,
    cluster_name: str,
    service_name: str,
    candidates: Dict[str, Dict[str, Any]],
    max_tasks: int = 50,
) -> None:
    try:
        response = ecs_client.list_tasks(
            cluster=cluster_name,
            serviceName=service_name,
            desiredStatus="STOPPED",
            maxResults=min(max_tasks, 100),
        )
    except ClientError:
        return

    task_arns = response.get("taskArns", [])
    if not task_arns:
        return

    for offset in range(0, len(task_arns), 100):
        batch = task_arns[offset : offset + 100]
        described = ecs_client.describe_tasks(cluster=cluster_name, tasks=batch)
        for task in described.get("tasks", []):
            stop_code = task.get("stopCode")
            if stop_code not in STABLE_STOP_CODES:
                continue

            task_definition_arn = task.get("taskDefinitionArn")
            if not task_definition_arn:
                continue

            started_at = task.get("startedAt")
            stopped_at = task.get("stoppedAt") or task.get("stoppingAt")
            if started_at and stopped_at and stopped_at <= started_at:
                continue

            _upsert_stable_candidate(
                candidates,
                task_definition_arn,
                stopped_at or started_at,
                "stopped_task",
                task_arn=task.get("taskArn"),
                stop_code=stop_code,
            )


def enrich_stable_task_candidates(
    ecs_client,
    candidates: Dict[str, Dict[str, Any]],
    cluster_name: str,
    service_name: str,
    current_task_definition: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    ordered = sorted(
        candidates.values(),
        key=lambda item: item.get("last_stable_at") or "",
        reverse=True,
    )[:limit]

    enriched: List[Dict[str, Any]] = []
    for item in ordered:
        task_definition_arn = item["task_definition_arn"]
        try:
            task_definition = describe_task_definition(ecs_client, task_definition_arn)
            container_images = get_container_images(task_definition)
        except ClientError:
            container_images = []

        primary_image = container_images[0]["image"] if container_images else None
        task_definition_short = item["task_definition"]
        enriched.append(
            {
                **item,
                "container_images": container_images,
                "image": primary_image,
                "is_current": task_definition_arn == current_task_definition,
                "rollback_command": build_rollback_command(
                    cluster_name,
                    service_name,
                    task_definition_short,
                ),
            }
        )

    return enriched


def parse_interval(value: str) -> int:
    """Parse interval like 10m, 30s, 1h into seconds."""
    text = value.strip().lower()
    match = re.fullmatch(r"(\d+)\s*([smhd]?)", text)
    if not match:
        raise SystemExit(
            f"Invalid --interval '{value}'. Use formats like 30s, 10m, 1h."
        )

    amount = int(match.group(1))
    unit = match.group(2) or "m"
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    seconds = amount * multipliers[unit]
    if seconds < 30:
        raise SystemExit("Interval must be at least 30 seconds.")
    return seconds


def parse_http_success_codes(matcher: Any) -> Set[int]:
    if isinstance(matcher, dict):
        matcher = matcher.get("HttpCode") or matcher.get("http_code")
    text = str(matcher or "200").replace(" ", "")
    codes: Set[int] = set()
    for part in text.split(","):
        if not part:
            continue
        try:
            if "-" in part:
                start, end = part.split("-", 1)
                codes.update(range(int(start), int(end) + 1))
            else:
                codes.add(int(part))
        except ValueError:
            continue
    return codes or {200}


def format_success_codes(codes: Set[int]) -> str:
    if not codes:
        return "200"
    ordered = sorted(codes)
    if ordered == list(range(ordered[0], ordered[-1] + 1)) and len(ordered) > 1:
        return f"{ordered[0]}-{ordered[-1]}"
    return ",".join(str(code) for code in ordered[:12])


def http_probe_settings(
    service_config: Dict[str, Any],
    checks_config: Dict[str, Any],
    target_health: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Prefer ALB target-group health check path/matcher when config is not explicit."""
    groups = (target_health or {}).get("target_groups") or []
    target_group = None
    for group in groups:
        protocol = str(group.get("health_check_protocol") or "").upper()
        if protocol in {"HTTP", "HTTPS"} or group.get("health_check_path"):
            target_group = group
            break
    if target_group is None and groups:
        target_group = groups[0]

    explicit_path = service_config.get("health_check_path") or checks_config.get(
        "http_health_path"
    )
    path = explicit_path or (target_group or {}).get("health_check_path") or "/health"
    if not str(path).startswith("/"):
        path = f"/{path}"

    scheme = service_config.get("health_check_scheme") or checks_config.get(
        "http_health_scheme"
    )
    if not scheme and target_group:
        protocol = str(target_group.get("health_check_protocol") or "").upper()
        if protocol == "HTTPS":
            scheme = "https"
        elif protocol == "HTTP":
            scheme = "http"
    scheme = scheme or "https"

    explicit_status = service_config.get("expected_http_status")
    if explicit_status is None:
        explicit_status = checks_config.get("http_expected_status")
    if explicit_status is not None:
        accepted = {int(explicit_status)}
        matcher_label = str(int(explicit_status))
        source = "config"
    elif target_group and target_group.get("health_check_matcher"):
        accepted = parse_http_success_codes(target_group.get("health_check_matcher"))
        matcher_label = str(target_group.get("health_check_matcher"))
        source = f"target group {target_group.get('name')}"
    else:
        accepted = {200}
        matcher_label = "200"
        source = "default"

    timeout = float(checks_config.get("http_timeout_seconds", 5))
    tg_timeout = (target_group or {}).get("health_check_timeout")
    if tg_timeout:
        try:
            timeout = max(float(tg_timeout), 1.0)
        except (TypeError, ValueError):
            pass

    return {
        "path": path,
        "scheme": scheme,
        "accepted_statuses": accepted,
        "expected_status": min(accepted) if accepted else 200,
        "matcher_label": matcher_label,
        "timeout_seconds": timeout,
        "method": str(checks_config.get("http_method", "GET")),
        "source": source,
        "target_group": (target_group or {}).get("name"),
    }


def resolve_http_health_url(
    service_config: Dict[str, Any],
    checks_config: Dict[str, Any],
    connectivity: Optional[Dict[str, Any]] = None,
    target_health: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    explicit = (
        service_config.get("health_check_url")
        or service_config.get("url")
        or checks_config.get("health_check_url")
    )
    if explicit:
        return str(explicit)

    settings = http_probe_settings(service_config, checks_config, target_health)
    path = settings["path"]
    scheme = settings["scheme"]

    hosts: List[str] = []
    if connectivity:
        hosts.extend(connectivity.get("http_hosts") or [])
        for record in connectivity.get("dns_records") or []:
            if record.get("name"):
                hosts.append(record["name"])
        for node in connectivity.get("nodes", []):
            if node.get("type") == "route53" and node.get("label"):
                hosts.append(node["label"])

    host = next((item for item in hosts if item and "*" not in str(item)), None)
    if not host:
        return None

    return f"{scheme}://{host}{path}"


def check_http_health(
    url: str,
    *,
    expected_status: int = 200,
    accepted_statuses: Optional[Set[int]] = None,
    timeout_seconds: float = 5.0,
    method: str = "GET",
) -> Dict[str, Any]:
    parsed = urlparse(url)
    accepted = set(accepted_statuses) if accepted_statuses else {expected_status}
    expected_label = format_success_codes(accepted)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {
            "status": STATUS_FAIL,
            "message": f"Invalid health check URL: {url}",
            "url": url,
            "expected_status": expected_status,
            "accepted_statuses": sorted(accepted),
        }

    request = urllib.request.Request(
        url,
        method=method.upper(),
        headers={"User-Agent": f"ecs-service-doctor/{VERSION}"},
    )

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 200))
            response.read(1024)
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        ok = status_code in accepted
        return {
            "status": STATUS_PASS if ok else STATUS_FAIL,
            "message": (
                f"HTTP {status_code} from {url} ({elapsed_ms}ms)"
                if ok
                else f"HTTP {status_code} from {url} (expected {expected_label})"
            ),
            "url": url,
            "http_status": status_code,
            "expected_status": expected_status,
            "accepted_statuses": sorted(accepted),
            "elapsed_ms": elapsed_ms,
        }
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "status": STATUS_FAIL,
            "message": f"HTTP check failed for {url}: {exc}",
            "url": url,
            "expected_status": expected_status,
            "accepted_statuses": sorted(accepted),
            "elapsed_ms": elapsed_ms,
        }

    elapsed_ms = int((time.monotonic() - started) * 1000)
    ok = status_code in accepted
    return {
        "status": STATUS_PASS if ok else STATUS_FAIL,
        "message": (
            f"HTTP {status_code} from {url} ({elapsed_ms}ms)"
            if ok
            else f"HTTP {status_code} from {url} (expected {expected_label})"
        ),
        "url": url,
        "http_status": status_code,
        "expected_status": expected_status,
        "accepted_statuses": sorted(accepted),
        "elapsed_ms": elapsed_ms,
    }


def collect_host_header_routes(
    connectivity: Optional[Dict[str, Any]],
    target_health: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Host-header listener rules that forward to this service's target groups."""
    groups = (target_health or {}).get("target_groups") or []
    service_arns = {
        group.get("target_group_arn") for group in groups if group.get("target_group_arn")
    }
    service_names = {group.get("name") for group in groups if group.get("name")}
    if not service_arns and not service_names:
        return []

    routes: List[Dict[str, Any]] = []
    by_host: Dict[str, Dict[str, Any]] = {}
    for load_balancer in (connectivity or {}).get("load_balancers") or []:
        for listener in load_balancer.get("listeners") or []:
            protocol = str(listener.get("protocol") or "HTTPS").upper()
            port = listener.get("port")
            scheme = "https" if protocol in {"HTTPS", "TLS"} else "http"
            for rule in listener.get("host_header_rules") or []:
                rule_arns = set(rule.get("target_group_arns") or [])
                rule_names = set(rule.get("target_groups") or [])
                if not (rule_arns & service_arns or rule_names & service_names):
                    continue
                for host in rule.get("hosts") or []:
                    route = {
                        "host": host,
                        "scheme": scheme,
                        "listener": (
                            f"{protocol}:{port}" if port is not None else protocol
                        ),
                        "load_balancer": load_balancer.get("name"),
                        "target_groups": rule.get("target_groups") or [],
                        "priority": rule.get("priority"),
                        "wildcard": "*" in str(host),
                    }
                    existing = by_host.get(host)
                    if existing and existing["scheme"] == "https":
                        continue
                    by_host[host] = route
    routes.extend(by_host.values())
    return routes


def _normalize_host(host: str) -> str:
    return str(host).rstrip(".").lower()


def _host_matches_pattern(name: str, pattern: str) -> bool:
    name = _normalize_host(name)
    pattern = _normalize_host(pattern)
    if "*" in pattern:
        return fnmatch.fnmatch(name, pattern)
    return name == pattern


def collect_route53_names(connectivity: Optional[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen: Set[str] = set()

    def add(name: Optional[str]) -> None:
        if not name or "*" in str(name):
            return
        key = _normalize_host(name)
        if key in seen:
            return
        seen.add(key)
        names.append(str(name).rstrip("."))

    for record in (connectivity or {}).get("dns_records") or []:
        add(record.get("name"))
    for load_balancer in (connectivity or {}).get("load_balancers") or []:
        for record in load_balancer.get("dns_records") or []:
            add(record.get("name"))
    for node in (connectivity or {}).get("nodes") or []:
        if node.get("type") == "route53":
            add(node.get("label"))
    return names


def collect_endpoint_hosts(
    connectivity: Optional[Dict[str, Any]],
    target_health: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Host-header routes plus Route 53 names that belong to this service."""
    routes = collect_host_header_routes(connectivity, target_health)
    dns_names = collect_route53_names(connectivity)
    endpoints: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def add(host: str, scheme: str, source: str, **extra: Any) -> None:
        key = _normalize_host(host)
        if not key or key in seen:
            return
        seen.add(key)
        endpoints.append(
            {
                "host": str(host).rstrip("."),
                "scheme": scheme,
                "source": source,
                "wildcard": "*" in str(host),
                **extra,
            }
        )

    for route in routes:
        add(
            route["host"],
            route["scheme"],
            "host-header",
            listener=route.get("listener"),
            load_balancer=route.get("load_balancer"),
            priority=route.get("priority"),
        )

    patterns = [route["host"] for route in routes]
    for name in dns_names:
        matched = next(
            (
                route
                for route in routes
                if _host_matches_pattern(name, route["host"])
            ),
            None,
        )
        if patterns and matched is None:
            continue
        extra = {}
        if matched:
            extra = {
                "listener": matched.get("listener"),
                "load_balancer": matched.get("load_balancer"),
                "priority": matched.get("priority"),
            }
        add(name, "https", "route53", **extra)

    return endpoints


def target_groups_support_http_probe(target_health: Optional[Dict[str, Any]]) -> bool:
    groups = (target_health or {}).get("target_groups") or []
    if not groups:
        return True
    for group in groups:
        protocol = str(
            group.get("health_check_protocol") or group.get("protocol") or ""
        ).upper()
        if protocol in {"HTTP", "HTTPS"} or group.get("health_check_path"):
            return True
    return False


def evaluate_host_header_health(
    connectivity: Optional[Dict[str, Any]],
    target_health: Optional[Dict[str, Any]],
    service_config: Dict[str, Any],
    checks_config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    endpoints = collect_endpoint_hosts(connectivity, target_health)
    if not endpoints:
        return None

    settings = http_probe_settings(service_config, checks_config, target_health)
    if not target_groups_support_http_probe(target_health) and not (
        service_config.get("health_check_url") or checks_config.get("health_check_url")
    ):
        return None

    path = settings["path"]
    method = settings["method"]
    probe_note = f"{method} {path} matcher {settings['matcher_label']}"
    if settings.get("target_group"):
        probe_note += f" (target group {settings['target_group']})"

    host_results: List[Dict[str, Any]] = []
    probes: List[Tuple[Dict[str, Any], str]] = []
    for endpoint in endpoints:
        if endpoint["wildcard"]:
            host_results.append(
                {
                    "host": endpoint["host"],
                    "status": STATUS_WARN,
                    "message": (
                        f"Wildcard host {endpoint['host']} cannot be probed"
                    ),
                    "wildcard": True,
                    "source": endpoint.get("source"),
                    "listener": endpoint.get("listener"),
                    "load_balancer": endpoint.get("load_balancer"),
                    "priority": endpoint.get("priority"),
                }
            )
            continue
        url = f"{endpoint['scheme']}://{endpoint['host']}{path}"
        probes.append((endpoint, url))

    def _probe(item: Tuple[Dict[str, Any], str]) -> Dict[str, Any]:
        endpoint, url = item
        result = check_http_health(
            url,
            expected_status=settings["expected_status"],
            accepted_statuses=settings["accepted_statuses"],
            timeout_seconds=settings["timeout_seconds"],
            method=method,
        )
        return {
            **result,
            "host": endpoint["host"],
            "listener": endpoint.get("listener"),
            "load_balancer": endpoint.get("load_balancer"),
            "source": endpoint.get("source"),
            "priority": endpoint.get("priority"),
            "wildcard": False,
        }

    if probes:
        workers = min(DEFAULT_MAX_WORKERS, len(probes))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            host_results.extend(executor.map(_probe, probes))

    failed = [item for item in host_results if item.get("status") == STATUS_FAIL]
    warned = [item for item in host_results if item.get("status") == STATUS_WARN]
    probed_ok = [
        item
        for item in host_results
        if item.get("status") == STATUS_PASS and not item.get("wildcard")
    ]
    rule_labels: List[str] = []
    for item in host_results:
        priority = item.get("priority")
        if priority is None or str(priority) == "":
            continue
        label = f"#{priority}"
        if label not in rule_labels:
            rule_labels.append(label)
    rules_note = f"listener rules {', '.join(rule_labels)}" if rule_labels else ""
    if failed:
        status = STATUS_FAIL
        hosts = ", ".join(item["host"] for item in failed)
        message = f"{len(failed)} endpoint(s) failed: {hosts}"
        if rules_note:
            message += f" · {rules_note}"
        message += f" · {probe_note}"
    elif probed_ok:
        status = STATUS_PASS
        message = f"{len(probed_ok)} endpoint(s) healthy"
        if rules_note:
            message += f" · {rules_note}"
        message += f" · {probe_note}"
        if warned:
            message += f" · {len(warned)} wildcard(s) skipped"
    else:
        status = STATUS_WARN
        message = f"Endpoints were found but none could be probed · {probe_note}"

    return {
        "status": status,
        "message": message,
        "hosts": host_results,
        "path": path,
        "matcher": settings["matcher_label"],
        "probe_source": settings["source"],
        "listener_rules": rule_labels,
    }


def evaluate_stable_task_history(
    ecs_client,
    cluster_name: str,
    service_name: str,
    service: Dict[str, Any],
    current_task_definition: Optional[str],
    limit: int = 3,
) -> Dict[str, Any]:
    candidates: Dict[str, Dict[str, Any]] = {}

    collect_stable_tasks_from_deployments(service, candidates)
    collect_stable_tasks_from_events(service, candidates)
    collect_stable_tasks_from_stopped_tasks(
        ecs_client,
        cluster_name,
        service_name,
        candidates,
    )

    stable_tasks = enrich_stable_task_candidates(
        ecs_client,
        candidates,
        cluster_name,
        service_name,
        current_task_definition,
        limit,
    )

    if not stable_tasks:
        return {
            "status": STATUS_PASS,
            "message": "No recent stable task definitions found",
            "stable_tasks": [],
        }

    summary_names = ", ".join(task["task_definition"] for task in stable_tasks)
    return {
        "status": STATUS_PASS,
        "message": f"{len(stable_tasks)} recent stable task definition(s): {summary_names}",
        "stable_tasks": stable_tasks,
    }


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


def describe_target_groups_batch(
    elbv2_client,
    target_group_arns: List[str],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """Return target group metadata and lookup failures."""
    metadata: Dict[str, Dict[str, Any]] = {}
    failures: Dict[str, str] = {}

    if not target_group_arns:
        return metadata, failures

    for offset in range(0, len(target_group_arns), 20):
        batch = target_group_arns[offset : offset + 20]
        response = elbv2_client.describe_target_groups(TargetGroupArns=batch)

        for target_group in response.get("TargetGroups", []):
            metadata[target_group["TargetGroupArn"]] = target_group

        for failure in response.get("Failures", []):
            arn = failure.get("TargetGroupArn") or failure.get("ResourceArn", "unknown")
            failures[arn] = failure.get("Reason", "Target group not found")

    for arn in target_group_arns:
        if arn not in metadata and arn not in failures:
            failures[arn] = "Target group not found"

    return metadata, failures


def _container_port_exists(
    task_definition: Dict[str, Any],
    container_name: str,
    container_port: int,
) -> bool:
    for container in task_definition.get("containerDefinitions", []):
        if container.get("name") != container_name:
            continue
        for mapping in container.get("portMappings", []):
            if mapping.get("containerPort") == container_port:
                return True
    return False


def _validate_target_group_attachment(
    *,
    attachment: Dict[str, Any],
    target_group: Optional[Dict[str, Any]],
    lookup_error: Optional[str],
    health: Dict[str, Any],
    task_definition: Optional[Dict[str, Any]],
    running_count: int,
) -> Dict[str, Any]:
    container_name = attachment.get("containerName")
    container_port = attachment.get("containerPort")
    target_group_arn = attachment.get("targetGroupArn", "")

    result: Dict[str, Any] = {
        "target_group_arn": target_group_arn,
        "ecs_container_name": container_name,
        "ecs_container_port": container_port,
        "attachment_ok": True,
        "attachment_issues": [],
    }

    if lookup_error or not target_group:
        result.update(
            {
                "name": target_group_arn.rsplit("/", 1)[-1],
                "attachment_ok": False,
                "attachment_issues": [
                    lookup_error or "Target group does not exist or is inaccessible"
                ],
            }
        )
        result.update(health)
        return result

    registered_total = sum(health.get("counts", {}).values())
    load_balancer_arns = target_group.get("LoadBalancerArns", [])

    result.update(
        {
            "name": target_group.get("TargetGroupName"),
            "port": target_group.get("Port"),
            "protocol": target_group.get("Protocol"),
            "target_type": target_group.get("TargetType"),
            "vpc_id": target_group.get("VpcId"),
            "health_check_path": target_group.get("HealthCheckPath"),
            "health_check_protocol": target_group.get("HealthCheckProtocol"),
            "health_check_port": target_group.get("HealthCheckPort"),
            "health_check_enabled": target_group.get("HealthCheckEnabled", True),
            "health_check_interval": target_group.get("HealthCheckIntervalSeconds"),
            "health_check_timeout": target_group.get("HealthCheckTimeoutSeconds"),
            "health_check_matcher": (target_group.get("Matcher") or {}).get("HttpCode"),
            "load_balancer_arns": load_balancer_arns,
            "load_balancer_count": len(load_balancer_arns),
            "registered_targets": registered_total,
        }
    )
    result.update(health)

    issues: List[str] = []

    if not load_balancer_arns:
        issues.append("Target group is not attached to any load balancer")

    if task_definition and container_name:
        container_names = [
            container.get("name")
            for container in task_definition.get("containerDefinitions", [])
        ]
        if container_name not in container_names:
            issues.append(
                f"ECS references container '{container_name}' but it is not in the task definition"
            )
        elif container_port is not None and not _container_port_exists(
            task_definition, container_name, container_port
        ):
            issues.append(
                f"ECS attachment port {container_port} on '{container_name}' "
                "does not match any task definition port mapping"
            )

    if (
        target_group.get("Port") is not None
        and container_port is not None
        and target_group.get("TargetType") == "ip"
        and target_group.get("Port") != container_port
    ):
        issues.append(
            f"Target group port {target_group.get('Port')} differs from ECS container port {container_port}"
        )

    if running_count > 0 and registered_total == 0:
        issues.append(
            f"No targets registered in target group while service has {running_count} running task(s)"
        )

    if health.get("counts", {}).get("unhealthy", 0) > 0:
        unhealthy = health["counts"]["unhealthy"]
        issues.append(f"{unhealthy} unhealthy target(s) registered")

    result["attachment_issues"] = issues
    result["attachment_ok"] = not issues
    return result


def evaluate_target_health(
    service: Dict[str, Any],
    elbv2_client,
    fail_on_unhealthy_targets: bool,
    max_workers: int = DEFAULT_MAX_WORKERS,
    task_definition: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    attachments = [
        load_balancer
        for load_balancer in service.get("loadBalancers", [])
        if load_balancer.get("targetGroupArn")
    ]
    classic_elb = [
        load_balancer
        for load_balancer in service.get("loadBalancers", [])
        if load_balancer.get("loadBalancerName") and not load_balancer.get("targetGroupArn")
    ]

    if not attachments:
        message = "No target group attached to service"
        if classic_elb:
            message = "Classic ELB detected; target group validation requires ALB/NLB"
        return {
            "status": STATUS_WARN,
            "message": message,
            "target_groups": [],
            "attachment_summary": message,
        }

    target_group_arns = [item["targetGroupArn"] for item in attachments]
    metadata, lookup_failures = describe_target_groups_batch(elbv2_client, target_group_arns)

    target_group_results = fetch_target_group_health_parallel(
        elbv2_client,
        target_group_arns,
        max_workers,
    )
    health_by_arn = {item["target_group_arn"]: item for item in target_group_results}

    running_count = service.get("runningCount", 0)
    enriched_groups = [
        _validate_target_group_attachment(
            attachment=attachment,
            target_group=metadata.get(attachment["targetGroupArn"]),
            lookup_error=lookup_failures.get(attachment["targetGroupArn"]),
            health=health_by_arn.get(
                attachment["targetGroupArn"],
                {"target_group_arn": attachment["targetGroupArn"], "counts": {}, "targets": []},
            ),
            task_definition=task_definition,
            running_count=running_count,
        )
        for attachment in attachments
    ]

    healthy_total = sum(r["counts"].get("healthy", 0) for r in enriched_groups)
    unhealthy_total = sum(r["counts"].get("unhealthy", 0) for r in enriched_groups)
    initial_total = sum(r["counts"].get("initial", 0) for r in enriched_groups)
    rollout_active = is_rollout_in_progress(service)

    attachment_failures = [
        issue
        for group in enriched_groups
        for issue in group.get("attachment_issues", [])
    ]
    all_attached = all(group.get("attachment_ok") for group in enriched_groups)

    attached_names = ", ".join(group.get("name", "unknown") for group in enriched_groups)
    attachment_summary = f"{len(enriched_groups)} target group(s): {attached_names}"
    if all_attached:
        attachment_summary += " — attachments look correct"
    else:
        attachment_summary += " — attachment problems detected"

    base = {
        "healthy_targets": healthy_total,
        "unhealthy_targets": unhealthy_total,
        "initial_targets": initial_total,
        "target_groups": enriched_groups,
        "attachment_summary": attachment_summary,
        "attachment_ok": all_attached,
    }

    if attachment_failures and fail_on_unhealthy_targets:
        return {
            **base,
            "status": STATUS_FAIL,
            "message": attachment_failures[0],
        }

    if unhealthy_total > 0 and fail_on_unhealthy_targets:
        return {
            **base,
            "status": STATUS_FAIL,
            "message": f"Unhealthy targets detected: {unhealthy_total}",
        }

    if not all_attached:
        return {
            **base,
            "status": STATUS_FAIL,
            "message": attachment_failures[0] if attachment_failures else "Target group attachment issue",
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
        "message": f"Target groups attached correctly: {', '.join(parts)}",
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
    sd_client,
    cluster_name: str,
    service_config: Dict[str, Any],
    checks_config: Dict[str, Any],
    prefetched_service: Optional[Dict[str, Any]] = None,
    prefetch_error: Optional[str] = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    route53_index: Optional[Dict[str, List[Any]]] = None,
    cloudwatch_client=None,
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

        task_definition: Optional[Dict[str, Any]] = None
        container_images: List[Dict[str, str]] = []

        if checks_config.get("include_task_definition", True) or checks_config.get(
            "include_connectivity_diagram", True
        ) or checks_config.get("include_target_group_health", True) or checks_config.get(
            "include_cpu_memory", True
        ):
            task_definition = describe_task_definition(
                ecs_client, service["taskDefinition"]
            )
            container_images = get_container_images(task_definition)

        if checks_config.get("include_task_definition", True) and task_definition:
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
                "container_images": container_images,
            }

        if checks_config.get("include_cpu_memory", True):
            utilization: Dict[str, Optional[float]] = {"cpu": None, "memory": None}
            lookback_minutes = int(checks_config.get("resource_lookback_minutes", 15))
            if cloudwatch_client is not None:
                try:
                    utilization = fetch_service_utilization(
                        cloudwatch_client,
                        cluster_name,
                        service_name,
                        lookback_minutes=lookback_minutes,
                    )
                except ClientError:
                    utilization = {"cpu": None, "memory": None}
            result["checks"]["resources"] = evaluate_cpu_memory(
                task_definition,
                utilization,
                cpu_warn_percent=float(checks_config.get("cpu_warn_percent", 80)),
                cpu_fail_percent=float(checks_config.get("cpu_fail_percent", 90)),
                memory_warn_percent=float(checks_config.get("memory_warn_percent", 80)),
                memory_fail_percent=float(checks_config.get("memory_fail_percent", 90)),
                lookback_minutes=lookback_minutes,
            )

        if checks_config.get("include_target_group_health", True):
            result["checks"]["target_group_health"] = evaluate_target_health(
                service,
                elbv2_client,
                checks_config.get("fail_on_unhealthy_targets", True),
                max_workers,
                task_definition,
            )

        if checks_config.get("include_connectivity_diagram", True):
            result["checks"]["connectivity"] = discover_connectivity(
                elbv2_client=elbv2_client,
                sd_client=sd_client,
                service=service,
                task_definition=task_definition,
                container_images=container_images,
                service_name=service_name,
                cluster_name=cluster_name,
                route53_index=route53_index or {},
            )

        if checks_config.get("include_http_health", True):
            connectivity = result["checks"].get("connectivity")
            target_health = result["checks"].get("target_group_health")
            settings = http_probe_settings(
                service_config, checks_config, target_health
            )
            health_url = resolve_http_health_url(
                service_config,
                checks_config,
                connectivity,
                target_health,
            )
            explicit_url = (
                service_config.get("health_check_url")
                or service_config.get("url")
                or checks_config.get("health_check_url")
            )
            can_probe = bool(explicit_url) or target_groups_support_http_probe(
                target_health
            )
            if health_url and can_probe:
                http_result = check_http_health(
                    health_url,
                    expected_status=settings["expected_status"],
                    accepted_statuses=settings["accepted_statuses"],
                    timeout_seconds=settings["timeout_seconds"],
                    method=settings["method"],
                )
                http_result["probe_source"] = settings["source"]
                http_result["matcher"] = settings["matcher_label"]
                http_result["path"] = settings["path"]
                result["checks"]["http_health"] = http_result
            elif explicit_url or checks_config.get("require_http_health"):
                result["checks"]["http_health"] = {
                    "status": STATUS_FAIL,
                    "message": "HTTP health check enabled but no URL could be resolved",
                    "expected_status": settings["expected_status"],
                    "matcher": settings["matcher_label"],
                }

        if checks_config.get("include_host_header_health", True):
            host_check = evaluate_host_header_health(
                result["checks"].get("connectivity"),
                result["checks"].get("target_group_health"),
                service_config,
                checks_config,
            )
            if host_check:
                result["checks"]["host_header_health"] = host_check

        result["peer_hints"] = collect_peer_hints(
            task_definition,
            result["checks"].get("connectivity"),
        )

        if checks_config.get("include_stable_task_history", True):
            stable_limit = checks_config.get("stable_task_limit", 3)
            result["checks"]["stable_tasks"] = evaluate_stable_task_history(
                ecs_client,
                cluster_name,
                service_name,
                service,
                service.get("taskDefinition"),
                stable_limit,
            )

        result["status"] = aggregate_check_status(
            {
                key: value
                for key, value in result["checks"].items()
                if key not in {"connectivity", "stable_tasks"}
            }
        )

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
    aws_config = config.get("aws", {})
    checks_config = config.get("checks", {})
    ecs_client = session.client("ecs")
    elbv2_client = session.client("elbv2")
    sd_client = session.client("servicediscovery")
    cloudwatch_client = (
        session.client("cloudwatch")
        if checks_config.get("include_cpu_memory", True)
        else None
    )
    max_workers = aws_config.get("max_workers", DEFAULT_MAX_WORKERS)

    route53_index: Dict[str, Any] = empty_route53_catalog()
    if checks_config.get("include_connectivity_diagram", True):
        try:
            route53_index = build_route53_index(session.client("route53"))
        except ClientError as exc:
            route53_index = empty_route53_catalog([str(exc)])

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
        "route53": route53_report_summary(route53_index),
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
                sd_client,
                cluster_name,
                service_config,
                checks_config,
                prefetched_service,
                prefetch_error,
                max_workers,
                route53_index,
                cloudwatch_client,
            )
            for cluster_name, service_config, prefetched_service, prefetch_error in work_items
        ]

        for future in futures:
            service_result = future.result()
            report["results"].append(service_result)
            update_summary(report["summary"], service_result)

    report["mesh"] = build_service_mesh(report["results"])
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
        if target_health.get("attachment_summary"):
            lines.append(f"  Target groups: {target_health['attachment_summary']}")

    http_health = checks.get("http_health")
    if http_health:
        if http_health.get("status") == STATUS_PASS:
            lines.append(f"  HTTP: {http_health.get('message', 'OK')}")
        else:
            lines.append(f"  HTTP: {http_health.get('message', 'not 200')}")

    host_headers = checks.get("host_header_health")
    if host_headers:
        if host_headers.get("status") == STATUS_PASS:
            lines.append(f"  Host headers: {host_headers.get('message', 'OK')}")
        else:
            lines.append(
                f"  Host headers: {host_headers.get('message', 'host-header check failed')}"
            )

    resources = checks.get("resources")
    if resources:
        cpu = resources.get("cpu") or {}
        memory = resources.get("memory") or {}
        if cpu.get("message"):
            lines.append(f"  CPU: {cpu['message']}")
        if memory.get("message"):
            lines.append(f"  Memory: {memory['message']}")

    stable_tasks = checks.get("stable_tasks", {})
    for task in stable_tasks.get("stable_tasks", [])[:3]:
        label = task.get("task_definition", "unknown")
        image = task.get("image")
        current = " (current)" if task.get("is_current") else ""
        if image:
            lines.append(f"  Stable task: {label}{current} — {image}")
        else:
            lines.append(f"  Stable task: {label}{current}")

    images = checks.get("task_definition", {}).get("container_images", [])
    if images:
        primary = images[0]
        lines.append(f"  Image: {primary['image']}")

    connectivity = checks.get("connectivity", {})
    dns_names = [
        record.get("name")
        for record in connectivity.get("dns_records") or []
        if record.get("name")
    ]
    if not dns_names:
        dns_names = [
            node.get("label")
            for node in connectivity.get("nodes") or []
            if node.get("type") == "route53" and node.get("label")
        ]
    if dns_names:
        unique = list(dict.fromkeys(dns_names))
        lines.append(f"  DNS: {', '.join(unique)}")
    if connectivity.get("summary"):
        lines.append(f"  Connectivity: {connectivity['summary']}")

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

        http_health = checks.get("http_health")
        if http_health:
            print(f"  HTTP Health     : [{http_health['status']}] {http_health['message']}")

        host_headers = checks.get("host_header_health")
        if host_headers:
            print(
                f"  Host headers    : [{host_headers['status']}] {host_headers['message']}"
            )

        connectivity = checks.get("connectivity") or {}
        dns_names = [
            record.get("name")
            for record in connectivity.get("dns_records") or []
            if record.get("name")
        ]
        if dns_names:
            print(f"  Route 53        : {', '.join(dict.fromkeys(dns_names))}")

        resources = checks.get("resources")
        if resources:
            cpu = resources.get("cpu") or {}
            memory = resources.get("memory") or {}
            if cpu.get("message"):
                print(f"  CPU             : [{cpu.get('status', resources['status'])}] {cpu['message']}")
            if memory.get("message"):
                print(f"  Memory          : [{memory.get('status', resources['status'])}] {memory['message']}")

        stable_tasks = checks.get("stable_tasks")
        if stable_tasks and stable_tasks.get("stable_tasks"):
            print(f"  Stable Tasks    : [{stable_tasks['status']}] {stable_tasks['message']}")
            for task in stable_tasks["stable_tasks"]:
                current = " (current)" if task.get("is_current") else ""
                image = task.get("image", "unknown")
                print(
                    f"    - {task.get('task_definition')}{current} "
                    f"last stable {task.get('last_stable_at', 'unknown')} -> {image}"
                )
                print(f"      Rollback: {task.get('rollback_command')}")

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


REPORT_TEMPLATE_PATH = Path(__file__).resolve().parent / "report-ui" / "report.template.html"
REPORT_JSON_PLACEHOLDER = "__ECS_REPORT_JSON__"


def serialize_report_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, default=str).replace("<", "\u003c")


def render_html_report(report: Dict[str, Any]) -> str:
    if not REPORT_TEMPLATE_PATH.is_file():
        raise RuntimeError(
            "HTML report template missing. Build it with: "
            "cd report-ui && npm install && npm run build"
        )

    template = REPORT_TEMPLATE_PATH.read_text(encoding="utf-8")
    payload = serialize_report_json(report)

    if REPORT_JSON_PLACEHOLDER not in template:
        raise RuntimeError("HTML template is missing the report data placeholder.")

    return template.replace(REPORT_JSON_PLACEHOLDER, payload, 1)


def build_sample_report() -> Dict[str, Any]:
    """Realistic demo data for documentation and examples."""
    report: Dict[str, Any] = {
        "tool": "ecs-service-doctor",
        "version": VERSION,
        "generated_at": "2026-08-07T20:53:00+00:00",
        "region": "us-east-1",
        "account_check": {
            "status": STATUS_PASS,
            "message": "Connected to AWS account 123456789012",
            "actual_account_id": "123456789012",
            "expected_account_id": "123456789012",
        },
        "summary": {
            "total_services": 7,
            "passed": 5,
            "warnings": 1,
            "failed": 1,
            "critical_failed": 1,
        },
        "route53": {
            "zones_scanned": 4,
            "records_scanned": 86,
            "errors": [],
        },
        "results": [
            {
                "cluster": "dev-apps-cluster",
                "service": "orders-api",
                "critical": True,
                "status": STATUS_PASS,
                "task_definition": "arn:aws:ecs:us-east-1:123456789012:task-definition/orders-api:42",
                "launch_type": "FARGATE",
                "platform_version": "LATEST",
                "checks": {
                    "task_counts": {
                        "status": STATUS_PASS,
                        "message": "Task counts look stable: desired=2, running=2, pending=0",
                        "desired": 2,
                        "running": 2,
                        "pending": 0,
                    },
                    "deployments": {
                        "status": STATUS_PASS,
                        "message": "Deployment state looks stable",
                    },
                    "target_group_health": {
                        "status": STATUS_PASS,
                        "message": "Target groups attached correctly: healthy=2, unhealthy=0",
                        "attachment_ok": True,
                        "attachment_summary": "1 target group(s): tg-orders — attachments look correct",
                        "target_groups": [
                            {
                                "name": "tg-orders",
                                "target_group_arn": "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/tg-orders/abc123",
                                "ecs_container_name": "orders-api",
                                "ecs_container_port": 8080,
                                "port": 8080,
                                "protocol": "HTTP",
                                "target_type": "ip",
                                "load_balancer_count": 1,
                                "registered_targets": 2,
                                "attachment_ok": True,
                                "attachment_issues": [],
                                "counts": {"healthy": 2, "unhealthy": 0, "initial": 0},
                            }
                        ],
                    },
                    "http_health": {
                        "status": STATUS_PASS,
                        "message": "HTTP 200 from https://api.example.com/health (42ms)",
                        "url": "https://api.example.com/health",
                        "http_status": 200,
                        "expected_status": 200,
                        "elapsed_ms": 42,
                    },
                    "task_definition": {
                        "status": STATUS_PASS,
                        "container_images": [
                            {
                                "container": "orders-api",
                                "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.3",
                            }
                        ],
                    },
                    "recent_events": {
                        "status": STATUS_PASS,
                        "events": [
                            {
                                "created_at": "2026-08-07T19:58:00+00:00",
                                "message": "(service orders-api) has reached a steady state.",
                            }
                        ],
                    },
                    "stable_tasks": {
                        "status": STATUS_PASS,
                        "message": "3 recent stable task definition(s): orders-api:42, orders-api:41, orders-api:40",
                        "stable_tasks": [
                            {
                                "task_definition_arn": "arn:aws:ecs:us-east-1:123456789012:task-definition/orders-api:42",
                                "task_definition": "orders-api:42",
                                "family": "orders-api",
                                "revision": 42,
                                "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.3",
                                "last_stable_at": "2026-08-07T19:58:00+00:00",
                                "source": "deployment",
                                "is_current": True,
                                "rollback_command": "aws ecs update-service --cluster dev-apps-cluster --service orders-api --task-definition orders-api:42 --force-new-deployment",
                            },
                            {
                                "task_definition_arn": "arn:aws:ecs:us-east-1:123456789012:task-definition/orders-api:41",
                                "task_definition": "orders-api:41",
                                "family": "orders-api",
                                "revision": 41,
                                "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.2",
                                "last_stable_at": "2026-08-05T14:20:00+00:00",
                                "source": "steady_state_event",
                                "is_current": False,
                                "rollback_command": "aws ecs update-service --cluster dev-apps-cluster --service orders-api --task-definition orders-api:41 --force-new-deployment",
                            },
                            {
                                "task_definition_arn": "arn:aws:ecs:us-east-1:123456789012:task-definition/orders-api:40",
                                "task_definition": "orders-api:40",
                                "family": "orders-api",
                                "revision": 40,
                                "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.1",
                                "last_stable_at": "2026-08-01T09:10:00+00:00",
                                "source": "stopped_task",
                                "is_current": False,
                                "rollback_command": "aws ecs update-service --cluster dev-apps-cluster --service orders-api --task-definition orders-api:40 --force-new-deployment",
                            },
                        ],
                    },
                    "connectivity": {
                        "status": STATUS_PASS,
                        "summary": "Route 53 → Load Balancer → Target Group → ECS → backend(s) → ECR",
                        "entrypoint": "r53_api_example_com",
                        "nodes": [
                            {"id": "r53_api_example_com", "type": "route53", "label": "api.example.com", "detail": "DNS record"},
                            {"id": "alb_dev_apps", "type": "alb", "label": "dev-apps-alb", "detail": "APPLICATION · internet-facing · active · HTTPS:443, HTTP:80", "dns_name": "dev-apps-alb-123.us-east-1.elb.amazonaws.com", "vpc_id": "vpc-0123456789abcdef0"},
                            {"id": "tg_tg_orders", "type": "target_group", "label": "tg-orders", "detail": "HTTP · 8080 → orders-api:8080"},
                            {"id": "ecs_orders_api", "type": "ecs_service", "label": "orders-api", "detail": "dev-apps-cluster"},
                            {"id": "rds_orders_db", "type": "rds", "label": "RDS: orders-db", "detail": "inferred backend"},
                            {"id": "ecr_orders_api", "type": "ecr", "label": "orders-api", "detail": "container image"},
                        ],
                        "edges": [
                            {"from": "r53_api_example_com", "to": "alb_dev_apps", "label": "alias"},
                            {"from": "alb_dev_apps", "to": "tg_tg_orders", "label": "forwards to"},
                            {"from": "tg_tg_orders", "to": "ecs_orders_api", "label": "registers tasks"},
                            {"from": "ecs_orders_api", "to": "rds_orders_db", "label": "connects"},
                            {"from": "ecr_orders_api", "to": "ecs_orders_api", "label": "pulls image"},
                        ],
                        "notes": [],
                        "load_balancers": [
                            {
                                "arn": "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/dev-apps-alb/abc",
                                "type": "alb",
                                "lb_type": "application",
                                "name": "dev-apps-alb",
                                "dns_name": "dev-apps-alb-123.us-east-1.elb.amazonaws.com",
                                "hosted_zone_id": "Z35SXDOTRQ7X7K",
                                "scheme": "internet-facing",
                                "state": "active",
                                "vpc_id": "vpc-0123456789abcdef0",
                                "ip_address_type": "ipv4",
                                "availability_zones": ["us-east-1a", "us-east-1b"],
                                "subnets": ["subnet-aaa111", "subnet-bbb222"],
                                "security_groups": ["sg-orders-alb"],
                                "listeners": [
                                    {
                                        "protocol": "HTTPS",
                                        "port": 443,
                                        "ssl_policy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
                                        "default_actions": ["forward → tg-orders"],
                                        "certificates": ["api.example.com"],
                                    },
                                    {
                                        "protocol": "HTTP",
                                        "port": 80,
                                        "default_actions": ["redirect → HTTPS:443"],
                                    },
                                ],
                            }
                        ],
                    },
                },
            },
            {
                "cluster": "dev-apps-cluster",
                "service": "agents-service",
                "critical": False,
                "status": STATUS_WARN,
                "task_definition": "arn:aws:ecs:us-east-1:123456789012:task-definition/agents-service:8",
                "launch_type": "FARGATE",
                "platform_version": "LATEST",
                "checks": {
                    "task_counts": {
                        "status": STATUS_PASS,
                        "message": "Task counts look stable: desired=1, running=1, pending=0",
                        "desired": 1,
                        "running": 1,
                        "pending": 0,
                    },
                    "deployments": {
                        "status": STATUS_WARN,
                        "message": "Multiple active deployments detected: 2",
                    },
                    "target_group_health": {
                        "status": STATUS_WARN,
                        "message": "Targets still registering during deployment: initial=1",
                        "attachment_ok": True,
                        "attachment_summary": "1 target group(s): tg-agents — attachments look correct",
                        "target_groups": [
                            {
                                "name": "tg-agents",
                                "ecs_container_name": "agents-service",
                                "ecs_container_port": 8080,
                                "port": 8080,
                                "protocol": "HTTP",
                                "registered_targets": 1,
                                "attachment_ok": True,
                                "attachment_issues": [],
                                "counts": {"healthy": 0, "unhealthy": 0, "initial": 1},
                            }
                        ],
                    },
                    "http_health": {
                        "status": STATUS_PASS,
                        "message": "HTTP 200 from https://agents.example.com/health (38ms)",
                        "url": "https://agents.example.com/health",
                        "http_status": 200,
                        "expected_status": 200,
                        "elapsed_ms": 38,
                    },
                    "task_definition": {
                        "status": STATUS_PASS,
                        "container_images": [
                            {
                                "container": "agents-service",
                                "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/agents-service:v0.9.0",
                            }
                        ],
                    },
                    "recent_events": {
                        "status": STATUS_PASS,
                        "events": [
                            {
                                "created_at": "2026-08-07T19:55:00+00:00",
                                "message": "(service agents-service) registered 1 targets in target-group tg-agents",
                            }
                        ],
                    },
                    "stable_tasks": {
                        "status": STATUS_PASS,
                        "message": "2 recent stable task definition(s): agents-service:7, agents-service:6",
                        "stable_tasks": [
                            {
                                "task_definition": "agents-service:7",
                                "family": "agents-service",
                                "revision": 7,
                                "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/agents-service:v0.8.9",
                                "last_stable_at": "2026-08-06T11:00:00+00:00",
                                "source": "deployment",
                                "is_current": False,
                                "rollback_command": "aws ecs update-service --cluster dev-apps-cluster --service agents-service --task-definition agents-service:7 --force-new-deployment",
                            },
                            {
                                "task_definition": "agents-service:6",
                                "family": "agents-service",
                                "revision": 6,
                                "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/agents-service:v0.8.8",
                                "last_stable_at": "2026-08-03T16:30:00+00:00",
                                "source": "steady_state_event",
                                "is_current": False,
                                "rollback_command": "aws ecs update-service --cluster dev-apps-cluster --service agents-service --task-definition agents-service:6 --force-new-deployment",
                            },
                        ],
                    },
                    "connectivity": {
                        "status": STATUS_PASS,
                        "summary": "Load Balancer → Target Group → ECS → ECR",
                        "entrypoint": "alb_dev_internal",
                        "nodes": [
                            {"id": "alb_dev_internal", "type": "alb", "label": "dev-internal-alb", "detail": "APPLICATION · internal"},
                            {"id": "tg_tg_agents", "type": "target_group", "label": "tg-agents", "detail": "HTTP · 8080 → agents-service:8080"},
                            {"id": "ecs_agents_service", "type": "ecs_service", "label": "agents-service", "detail": "dev-apps-cluster"},
                            {"id": "ecr_agents_service", "type": "ecr", "label": "agents-service", "detail": "container image"},
                        ],
                        "edges": [
                            {"from": "alb_dev_internal", "to": "tg_tg_agents", "label": "forwards to"},
                            {"from": "tg_tg_agents", "to": "ecs_agents_service", "label": "registers tasks"},
                            {"from": "ecr_agents_service", "to": "ecs_agents_service", "label": "pulls image"},
                        ],
                        "notes": [],
                        "load_balancers": [
                            {
                                "arn": "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/dev-internal-alb/def",
                                "type": "alb",
                                "lb_type": "application",
                                "name": "dev-internal-alb",
                                "dns_name": "internal-dev-internal-alb-456.us-east-1.elb.amazonaws.com",
                                "scheme": "internal",
                                "state": "active",
                                "vpc_id": "vpc-0123456789abcdef0",
                                "ip_address_type": "ipv4",
                                "availability_zones": ["us-east-1a", "us-east-1b"],
                                "security_groups": ["sg-internal-alb"],
                                "listeners": [
                                    {
                                        "protocol": "HTTP",
                                        "port": 80,
                                        "default_actions": ["forward → tg-agents"],
                                    }
                                ],
                            }
                        ],
                    },
                },
            },
            {
                "cluster": "dev-apps-cluster",
                "service": "payments-api",
                "critical": True,
                "status": STATUS_FAIL,
                "task_definition": "arn:aws:ecs:us-east-1:123456789012:task-definition/payments-api:17",
                "launch_type": "FARGATE",
                "platform_version": "LATEST",
                "checks": {
                    "task_counts": {
                        "status": STATUS_FAIL,
                        "message": "Running count is below desired count: running=1, desired=2",
                        "desired": 2,
                        "running": 1,
                        "pending": 0,
                    },
                    "deployments": {
                        "status": STATUS_FAIL,
                        "message": "Primary deployment rollout state is IN_PROGRESS",
                    },
                    "target_group_health": {
                        "status": STATUS_FAIL,
                        "message": "1 unhealthy target(s) registered",
                        "attachment_ok": False,
                        "attachment_summary": "1 target group(s): tg-payments — attachment problems detected",
                        "target_groups": [
                            {
                                "name": "tg-payments",
                                "ecs_container_name": "payments-api",
                                "ecs_container_port": 8080,
                                "port": 8080,
                                "protocol": "HTTP",
                                "registered_targets": 2,
                                "attachment_ok": False,
                                "attachment_issues": ["1 unhealthy target(s) registered"],
                                "counts": {"healthy": 1, "unhealthy": 1, "initial": 0},
                            }
                        ],
                    },
                    "http_health": {
                        "status": STATUS_FAIL,
                        "message": "HTTP 503 from https://payments.example.com/health (expected 200)",
                        "url": "https://payments.example.com/health",
                        "http_status": 503,
                        "expected_status": 200,
                        "elapsed_ms": 88,
                    },
                    "task_definition": {
                        "status": STATUS_PASS,
                        "container_images": [
                            {
                                "container": "payments-api",
                                "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/payments-api:v2.0.1",
                            }
                        ],
                    },
                    "recent_events": {
                        "status": STATUS_PASS,
                        "events": [
                            {
                                "created_at": "2026-08-07T19:52:00+00:00",
                                "message": "(service payments-api) was unable to place a task. Reason: ResourceInitializationError.",
                            },
                            {
                                "created_at": "2026-08-07T19:50:00+00:00",
                                "message": "(service payments-api) (task abc123) (port 8080) is unhealthy in target-group tg-payments due to Health checks failed.",
                            },
                        ],
                    },
                    "stable_tasks": {
                        "status": STATUS_PASS,
                        "message": "3 recent stable task definition(s): payments-api:16, payments-api:15, payments-api:14",
                        "stable_tasks": [
                            {
                                "task_definition": "payments-api:16",
                                "family": "payments-api",
                                "revision": 16,
                                "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/payments-api:v2.0.0",
                                "last_stable_at": "2026-08-06T08:00:00+00:00",
                                "source": "deployment",
                                "is_current": False,
                                "rollback_command": "aws ecs update-service --cluster dev-apps-cluster --service payments-api --task-definition payments-api:16 --force-new-deployment",
                            },
                            {
                                "task_definition": "payments-api:15",
                                "family": "payments-api",
                                "revision": 15,
                                "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/payments-api:v1.9.9",
                                "last_stable_at": "2026-08-04T12:00:00+00:00",
                                "source": "steady_state_event",
                                "is_current": False,
                                "rollback_command": "aws ecs update-service --cluster dev-apps-cluster --service payments-api --task-definition payments-api:15 --force-new-deployment",
                            },
                            {
                                "task_definition": "payments-api:14",
                                "family": "payments-api",
                                "revision": 14,
                                "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/payments-api:v1.9.8",
                                "last_stable_at": "2026-08-02T18:00:00+00:00",
                                "source": "stopped_task",
                                "is_current": False,
                                "rollback_command": "aws ecs update-service --cluster dev-apps-cluster --service payments-api --task-definition payments-api:14 --force-new-deployment",
                            },
                        ],
                    },
                    "connectivity": {
                        "status": STATUS_PASS,
                        "summary": "Internet → Load Balancer → Target Group → ECS → backend(s) → ECR",
                        "entrypoint": "internet_payments_api",
                        "nodes": [
                            {"id": "internet_payments_api", "type": "internet", "label": "Internet", "detail": "public load balancer"},
                            {"id": "alb_payments", "type": "alb", "label": "payments-alb", "detail": "APPLICATION · internet-facing"},
                            {"id": "tg_tg_payments", "type": "target_group", "label": "tg-payments", "detail": "HTTP · 8080 → payments-api:8080"},
                            {"id": "ecs_payments_api", "type": "ecs_service", "label": "payments-api", "detail": "dev-apps-cluster"},
                            {"id": "rds_payments_db", "type": "rds", "label": "RDS: payments-db", "detail": "inferred backend"},
                            {"id": "ecr_payments_api", "type": "ecr", "label": "payments-api", "detail": "container image"},
                        ],
                        "edges": [
                            {"from": "internet_payments_api", "to": "alb_payments", "label": "HTTPS/HTTP"},
                            {"from": "alb_payments", "to": "tg_tg_payments", "label": "forwards to"},
                            {"from": "tg_tg_payments", "to": "ecs_payments_api", "label": "registers tasks"},
                            {"from": "ecs_payments_api", "to": "rds_payments_db", "label": "connects"},
                            {"from": "ecr_payments_api", "to": "ecs_payments_api", "label": "pulls image"},
                        ],
                        "notes": [],
                        "load_balancers": [
                            {
                                "arn": "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/payments-alb/ghi",
                                "type": "alb",
                                "lb_type": "application",
                                "name": "payments-alb",
                                "dns_name": "payments-alb-789.us-east-1.elb.amazonaws.com",
                                "scheme": "internet-facing",
                                "state": "active",
                                "vpc_id": "vpc-0123456789abcdef0",
                                "ip_address_type": "dualstack",
                                "availability_zones": ["us-east-1a", "us-east-1c"],
                                "security_groups": ["sg-payments-alb"],
                                "listeners": [
                                    {
                                        "protocol": "HTTPS",
                                        "port": 443,
                                        "ssl_policy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
                                        "default_actions": ["forward → tg-payments"],
                                    }
                                ],
                            }
                        ],
                    },
                },
            },
            *_sample_healthy_services(),
        ],
    }

    resource_samples = {
        "orders-api": (512, 1024, 18.0, 41.0),
        "agents-service": (256, 512, 81.0, 47.0),
        "payments-api": (1024, 2048, 64.0, 93.0),
        "catalog-api": (256, 512, 12.0, 28.0),
        "search-api": (512, 1024, 22.0, 35.0),
        "billing-api": (256, 512, 9.0, 24.0),
        "notifications-worker": (256, 512, 6.0, 19.0),
    }
    for item in report["results"]:
        cpu, memory, cpu_util, memory_util = resource_samples.get(
            item["service"], (256, 512, 10.0, 20.0)
        )
        item["checks"]["resources"] = evaluate_cpu_memory(
            {"cpu": str(cpu), "memory": str(memory)},
            {"cpu": cpu_util, "memory": memory_util},
        )
        task_definition = item["checks"].setdefault("task_definition", {})
        task_definition["cpu"] = str(cpu)
        task_definition["memory"] = str(memory)

    dns_by_lb = {
        "dev-apps-alb": [
            {
                "name": "api.example.com",
                "type": "A",
                "alias": True,
                "zone_name": "example.com",
                "target": "dev-apps-alb-123.us-east-1.elb.amazonaws.com",
            },
            {
                "name": "www.example.com",
                "type": "A",
                "alias": True,
                "zone_name": "example.com",
                "target": "dev-apps-alb-123.us-east-1.elb.amazonaws.com",
            },
        ],
        "payments-alb": [
            {
                "name": "payments.example.com",
                "type": "A",
                "alias": True,
                "zone_name": "example.com",
                "target": "payments-alb-789.us-east-1.elb.amazonaws.com",
            }
        ],
        "dev-internal-alb": [
            {
                "name": "agents.internal.example.com",
                "type": "A",
                "alias": True,
                "zone_name": "internal.example.com",
                "target": "internal-dev-internal-alb-456.us-east-1.elb.amazonaws.com",
            }
        ],
    }
    for item in report["results"]:
        connectivity = item["checks"].setdefault("connectivity", {})
        records_out: List[Dict[str, Any]] = []
        for load_balancer in connectivity.get("load_balancers") or []:
            records = dns_by_lb.get(load_balancer.get("name"), [])
            load_balancer["dns_records"] = records
            for record in records:
                records_out.append(
                    {
                        **record,
                        "load_balancer": load_balancer.get("name"),
                        "load_balancer_dns": load_balancer.get("dns_name"),
                    }
                )
        if records_out:
            connectivity["dns_records"] = records_out

    host_headers_by_service = {
        "orders-api": ["api.example.com", "www.example.com"],
        "agents-service": ["agents.internal.example.com"],
        "payments-api": ["payments.example.com"],
        "catalog-api": ["catalog-api.example.com"],
        "search-api": ["search-api.example.com"],
        "billing-api": ["billing-api.example.com"],
    }
    tg_name_by_service = {
        "orders-api": "tg-orders",
        "agents-service": "tg-agents",
        "payments-api": "tg-payments",
    }
    for item in report["results"]:
        hosts = host_headers_by_service.get(item["service"])
        if not hosts:
            continue
        tg_name = tg_name_by_service.get(item["service"], f"tg-{item['service']}")
        connectivity = item["checks"].setdefault("connectivity", {})
        for load_balancer in connectivity.get("load_balancers") or []:
            for listener in load_balancer.get("listeners") or []:
                listener["host_header_rules"] = [
                    {
                        "priority": str(10 + index * 10),
                        "hosts": [host],
                        "target_groups": [tg_name],
                        "target_group_arns": [],
                        "action": f"forward → {tg_name}",
                    }
                    for index, host in enumerate(hosts)
                ]
        failed = item["service"] == "payments-api"
        host_results = []
        for index, host in enumerate(hosts):
            url = f"https://{host}/health"
            priority = str(10 + index * 10)
            if failed:
                host_results.append(
                    {
                        "host": host,
                        "status": STATUS_FAIL,
                        "message": f"HTTP 503 from {url} (expected 200)",
                        "url": url,
                        "http_status": 503,
                        "expected_status": 200,
                        "wildcard": False,
                        "source": "host-header",
                        "priority": priority,
                    }
                )
            else:
                host_results.append(
                    {
                        "host": host,
                        "status": STATUS_PASS,
                        "message": f"HTTP 200 from {url} (28ms)",
                        "url": url,
                        "http_status": 200,
                        "expected_status": 200,
                        "elapsed_ms": 28,
                        "wildcard": False,
                        "source": "host-header",
                        "priority": priority,
                    }
                )
        rule_labels = [f"#{10 + index * 10}" for index in range(len(hosts))]
        probe_note = f"GET /health matcher 200 (target group {tg_name})"
        rules_note = f"listener rules {', '.join(rule_labels)}"
        item["checks"]["host_header_health"] = {
            "status": STATUS_FAIL if failed else STATUS_PASS,
            "message": (
                f"{len(hosts)} endpoint(s) failed: {', '.join(hosts)} · {rules_note} · {probe_note}"
                if failed
                else f"{len(hosts)} endpoint(s) healthy · {rules_note} · {probe_note}"
            ),
            "hosts": host_results,
            "path": "/health",
            "matcher": "200",
            "probe_source": f"target group {tg_name}",
            "listener_rules": rule_labels,
        }

    peer_hints = {
        "orders-api": [
            "https://payments-api.example.com",
            "PAYMENTS_URL",
            "https://search-api.example.com",
        ],
        "agents-service": ["https://orders-api.example.com", "ORDERS_API_URL"],
        "catalog-api": ["https://search-api.example.com", "SEARCH_API_URL"],
        "search-api": ["https://orders-api.example.com"],
        "billing-api": ["https://payments-api.example.com", "PAYMENTS_API"],
        "notifications-worker": [
            "https://orders-api.example.com",
            "https://agents-service.example.com",
        ],
        "payments-api": ["https://orders-api.example.com"],
    }
    for item in report["results"]:
        item["peer_hints"] = peer_hints.get(item["service"], [])
    report["mesh"] = build_service_mesh(report["results"])
    for item in report["results"]:
        for group in (
            item.get("checks", {}).get("target_group_health", {}).get("target_groups")
            or []
        ):
            group.setdefault("health_check_path", "/health")
            group.setdefault("health_check_protocol", "HTTP")
            group.setdefault("health_check_port", "traffic-port")
            group.setdefault("health_check_enabled", True)
            group.setdefault("health_check_interval", 30)
            group.setdefault("health_check_timeout", 5)
            group.setdefault("health_check_matcher", "200")
    return report


def _sample_healthy_services() -> List[Dict[str, Any]]:
    names = [
        ("catalog-api", "catalog-api", 3),
        ("search-api", "search-api", 2),
        ("billing-api", "billing-api", 2),
        ("notifications-worker", "notifications-worker", 1),
    ]
    services: List[Dict[str, Any]] = []
    for name, image, desired in names:
        services.append(
            {
                "cluster": "dev-apps-cluster",
                "service": name,
                "critical": False,
                "status": STATUS_PASS,
                "task_definition": (
                    f"arn:aws:ecs:us-east-1:123456789012:task-definition/{name}:12"
                ),
                "launch_type": "FARGATE",
                "platform_version": "LATEST",
                "checks": {
                    "task_counts": {
                        "status": STATUS_PASS,
                        "message": (
                            f"Task counts look stable: desired={desired}, "
                            f"running={desired}, pending=0"
                        ),
                        "desired": desired,
                        "running": desired,
                        "pending": 0,
                    },
                    "deployments": {
                        "status": STATUS_PASS,
                        "message": "Deployment state looks stable",
                    },
                    "target_group_health": {
                        "status": STATUS_PASS,
                        "message": (
                            f"Target groups attached correctly: healthy={desired}, unhealthy=0"
                        ),
                        "attachment_ok": True,
                        "attachment_summary": (
                            f"1 target group(s): tg-{name} — attachments look correct"
                        ),
                        "target_groups": (
                            []
                            if name == "notifications-worker"
                            else [
                                {
                                    "name": f"tg-{name}",
                                    "target_group_arn": (
                                        "arn:aws:elasticloadbalancing:us-east-1:"
                                        f"123456789012:targetgroup/tg-{name}/sample"
                                    ),
                                    "ecs_container_name": name,
                                    "ecs_container_port": 8080,
                                    "port": 8080,
                                    "protocol": "HTTP",
                                    "target_type": "ip",
                                    "load_balancer_count": 1,
                                    "registered_targets": desired,
                                    "attachment_ok": True,
                                    "attachment_issues": [],
                                    "counts": {
                                        "healthy": desired,
                                        "unhealthy": 0,
                                        "initial": 0,
                                    },
                                }
                            ]
                        ),
                    },
                    "connectivity": {
                        "status": STATUS_PASS,
                        "summary": "Load Balancer → Target Group → ECS",
                        "load_balancers": (
                            []
                            if name == "notifications-worker"
                            else [
                                {
                                    "arn": (
                                        "arn:aws:elasticloadbalancing:us-east-1:"
                                        "123456789012:loadbalancer/app/dev-apps-alb/abc"
                                    ),
                                    "type": "alb",
                                    "lb_type": "application",
                                    "name": "dev-apps-alb",
                                    "dns_name": (
                                        "dev-apps-alb-123.us-east-1.elb.amazonaws.com"
                                    ),
                                    "scheme": "internet-facing",
                                    "state": "active",
                                    "vpc_id": "vpc-0123456789abcdef0",
                                    "ip_address_type": "ipv4",
                                    "availability_zones": ["us-east-1a", "us-east-1b"],
                                    "security_groups": ["sg-orders-alb"],
                                    "listeners": [
                                        {
                                            "protocol": "HTTPS",
                                            "port": 443,
                                            "ssl_policy": (
                                                "ELBSecurityPolicy-TLS13-1-2-2021-06"
                                            ),
                                            "default_actions": [f"forward → tg-{name}"],
                                        }
                                    ],
                                }
                            ]
                        ),
                    },
                    "http_health": {
                        "status": STATUS_PASS,
                        "message": f"HTTP 200 from https://{name}.example.com/health (30ms)",
                        "url": f"https://{name}.example.com/health",
                        "http_status": 200,
                        "expected_status": 200,
                        "elapsed_ms": 30,
                    },
                    "task_definition": {
                        "status": STATUS_PASS,
                        "container_images": [
                            {
                                "container": name,
                                "image": (
                                    "123456789012.dkr.ecr.us-east-1.amazonaws.com/"
                                    f"{image}:v1.0.0"
                                ),
                            }
                        ],
                    },
                    "recent_events": {
                        "status": STATUS_PASS,
                        "events": [
                            {
                                "created_at": "2026-08-07T19:58:00+00:00",
                                "message": f"(service {name}) has reached a steady state.",
                            }
                        ],
                    },
                },
            }
        )
    return services


def apply_cli_overrides(config: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    checks = config.setdefault("checks", {})
    notifications = config.setdefault("notifications", {})

    if getattr(args, "health_url", None):
        checks["health_check_url"] = args.health_url
        checks["include_http_health"] = True
        # Apply to every listed service when using CLI URL.
        for cluster in config.get("clusters", []):
            for service in cluster.get("services", []):
                service.setdefault("health_check_url", args.health_url)

    if getattr(args, "health_path", None):
        checks["http_health_path"] = args.health_path
        checks["include_http_health"] = True

    if getattr(args, "expected_http_status", None) is not None:
        checks["http_expected_status"] = args.expected_http_status

    if getattr(args, "notify_slack", None):
        notifications["slack_webhook_url"] = args.notify_slack
    if getattr(args, "notify_teams", None):
        notifications["teams_webhook_url"] = args.notify_teams
    if getattr(args, "notify_webhook", None):
        notifications["webhook_url"] = args.notify_webhook
    if getattr(args, "notify_sns", None):
        notifications["sns_topic_arn"] = args.notify_sns
    if getattr(args, "notify_on_warn", False):
        notifications["on_warn"] = True

    return config


def run_once(
    config: Dict[str, Any],
    args: argparse.Namespace,
    *,
    last_fingerprint: Optional[str] = None,
) -> Tuple[Dict[str, Any], int, Optional[str]]:
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

    notifications_config = config.get("notifications", {})
    fingerprint = fingerprint_unhealthy(report)
    notify_now = should_notify(
        report,
        on_fail=notifications_config.get("on_fail", True),
        on_warn=notifications_config.get("on_warn", False),
    )

    # Avoid spamming the same alert every interval tick.
    if notify_now and fingerprint and fingerprint == last_fingerprint:
        print("Notifications: skipped (same unhealthy fingerprint as last run)")
        notify_now = False

    if notify_now and (
        notifications_config.get("slack_webhook_url")
        or notifications_config.get("teams_webhook_url")
        or notifications_config.get("webhook_url")
        or notifications_config.get("sns_topic_arn")
    ):
        session = create_session(config)
        results = dispatch_notifications(report, notifications_config, session=session)
        for item in results:
            channel = item.get("channel", "notify")
            if item.get("ok"):
                print(f"Notification sent via {channel}")
            else:
                print(
                    f"Notification failed via {channel}: {item.get('error') or item.get('status')}",
                    file=sys.stderr,
                )
        report["notifications"] = results
    elif notify_now:
        print(
            "Services unhealthy, but no notification channel configured "
            "(use --notify-slack, --notify-teams, --notify-webhook, or --notify-sns)."
        )

    return report, compute_exit_code(report), fingerprint if should_notify(
        report,
        on_fail=notifications_config.get("on_fail", True),
        on_warn=notifications_config.get("on_warn", False),
    ) else None


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

Continuous monitoring with HTTP 200 alerts:
  %(prog)s --config config.json --interval 10m --notify-slack https://hooks.slack.com/...
  %(prog)s --config config.json --interval 10m --notify-teams https://outlook.office.com/webhook/...
  %(prog)s -c my-cluster -s my-api --health-url https://api.example.com/health --interval 10m

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
        "--interval",
        metavar="DURATION",
        help="Run continuously (e.g. 10m, 30s, 1h). Ctrl+C to stop.",
    )

    parser.add_argument(
        "--health-url",
        metavar="URL",
        help="HTTP health check URL (overrides target-group path and host detection)",
    )

    parser.add_argument(
        "--health-path",
        metavar="PATH",
        default=None,
        help="Health path (default: ALB target-group HealthCheckPath, else /health)",
    )

    parser.add_argument(
        "--expected-http-status",
        type=int,
        default=None,
        metavar="CODE",
        help="Override expected HTTP status (default: ALB Matcher.HttpCode, else 200)",
    )

    parser.add_argument(
        "--notify-slack",
        metavar="WEBHOOK_URL",
        help="Slack incoming webhook URL for unhealthy alerts",
    )

    parser.add_argument(
        "--notify-teams",
        metavar="WEBHOOK_URL",
        help="Microsoft Teams incoming webhook URL for unhealthy alerts",
    )

    parser.add_argument(
        "--notify-webhook",
        metavar="URL",
        help="Generic JSON webhook URL for unhealthy alerts",
    )

    parser.add_argument(
        "--notify-sns",
        metavar="TOPIC_ARN",
        help="AWS SNS topic ARN for unhealthy alerts",
    )

    parser.add_argument(
        "--notify-on-warn",
        action="store_true",
        help="Also notify on WARN status (default: FAIL only)",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"ecs-service-doctor {VERSION}",
    )

    args = parser.parse_args()

    try:
        config = apply_cli_overrides(resolve_input_config(args), args)

        if not args.interval:
            _report, exit_code, _fingerprint = run_once(config, args)
            return exit_code

        interval_seconds = parse_interval(args.interval)
        print(
            f"Continuous monitoring every {args.interval} "
            f"({interval_seconds}s). Press Ctrl+C to stop."
        )
        last_fingerprint: Optional[str] = None
        last_exit = 0
        while True:
            print()
            print(f"=== Check at {utc_now()} ===")
            _report, last_exit, last_fingerprint = run_once(
                config,
                args,
                last_fingerprint=last_fingerprint,
            )
            print(f"Next check in {args.interval}...")
            time.sleep(interval_seconds)

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
