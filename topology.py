"""Discover rough ECS connectivity (LB, Route 53, backends) and build diagrams."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

NODE_ALB = "alb"
NODE_NLB = "nlb"
NODE_ECS = "ecs_service"
NODE_ROUTE53 = "route53"
NODE_RDS = "rds"
NODE_REDIS = "redis"
NODE_DOCDB = "docdb"
NODE_DYNAMODB = "dynamodb"
NODE_CLOUD_MAP = "cloud_map"
NODE_ECR = "ecr"
NODE_INTERNET = "internet"
NODE_GENERIC = "backend"

DB_HOST_KEYS = {
    "DATABASE_URL",
    "DB_HOST",
    "DB_HOSTNAME",
    "RDS_HOSTNAME",
    "MYSQL_HOST",
    "POSTGRES_HOST",
    "PGHOST",
    "POSTGRESQL_HOST",
    "MONGO_URI",
    "MONGODB_URI",
    "REDIS_URL",
    "REDIS_HOST",
    "ELASTICACHE_ENDPOINT",
    "DYNAMODB_ENDPOINT",
    "AWS_ENDPOINT_URL",
}

BACKEND_PATTERNS: List[Tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\.rds\.amazonaws\.com", re.I), NODE_RDS, "RDS"),
    (re.compile(r"\.cache\.amazonaws\.com", re.I), NODE_REDIS, "ElastiCache"),
    (re.compile(r"\.docdb\.amazonaws\.com", re.I), NODE_DOCDB, "DocumentDB"),
    (re.compile(r"\.dynamodb\.", re.I), NODE_DYNAMODB, "DynamoDB"),
    (re.compile(r"\.amazonaws\.com", re.I), NODE_GENERIC, "AWS service"),
]


def _node_id(prefix: str, label: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", label.lower())[:40]
    return f"{prefix}_{safe}"


def build_route53_index(route53_client) -> Dict[str, List[str]]:
    """Map DNS target (ALB/LB hostname) -> record names pointing at it."""
    index: Dict[str, List[str]] = {}

    try:
        zone_paginator = route53_client.get_paginator("list_hosted_zones")
        for zone_page in zone_paginator.paginate():
            for zone in zone_page.get("HostedZones", []):
                zone_id = zone["Id"].split("/")[-1]
                record_paginator = route53_client.get_paginator("list_resource_record_sets")
                for record_page in record_paginator.paginate(HostedZoneId=zone_id):
                    for record in record_page.get("ResourceRecordSets", []):
                        record_name = record.get("Name", "").rstrip(".")
                        alias = record.get("AliasTarget")
                        if alias:
                            target = alias.get("DNSName", "").rstrip(".")
                            index.setdefault(target, []).append(record_name)
                        for rr in record.get("ResourceRecords", []):
                            target = rr.get("Value", "").rstrip(".")
                            index.setdefault(target, []).append(record_name)
    except Exception:
        return {}

    return index


def discover_load_balancers(elbv2_client, service: Dict[str, Any]) -> List[Dict[str, Any]]:
    target_group_arns = [
        item["targetGroupArn"]
        for item in service.get("loadBalancers", [])
        if item.get("targetGroupArn")
    ]
    if not target_group_arns:
        return []

    load_balancers: Dict[str, Dict[str, Any]] = {}

    for offset in range(0, len(target_group_arns), 20):
        batch = target_group_arns[offset : offset + 20]
        response = elbv2_client.describe_target_groups(TargetGroupArns=batch)
        lb_arns: Set[str] = set()
        tg_by_lb: Dict[str, str] = {}

        for target_group in response.get("TargetGroups", []):
            tg_name = target_group.get("TargetGroupName", "target-group")
            for lb_arn in target_group.get("LoadBalancerArns", []):
                lb_arns.add(lb_arn)
                tg_by_lb[lb_arn] = tg_name

        if not lb_arns:
            continue

        lb_response = elbv2_client.describe_load_balancers(
            LoadBalancerArns=list(lb_arns)
        )
        for load_balancer in lb_response.get("LoadBalancers", []):
            arn = load_balancer["LoadBalancerArn"]
            lb_type = load_balancer.get("Type", "application")
            load_balancers[arn] = {
                "arn": arn,
                "type": NODE_ALB if lb_type == "application" else NODE_NLB,
                "lb_type": lb_type,
                "name": load_balancer.get("LoadBalancerName", "load-balancer"),
                "dns_name": load_balancer.get("DNSName", "").rstrip("."),
                "scheme": load_balancer.get("Scheme", "unknown"),
                "target_group": tg_by_lb.get(arn),
            }

    return list(load_balancers.values())


def discover_service_registries(sd_client, service: Dict[str, Any]) -> List[Dict[str, Any]]:
    discovered: List[Dict[str, Any]] = []

    for registry in service.get("serviceRegistries", []):
        registry_arn = registry.get("registryArn", "")
        if "/service/" not in registry_arn:
            continue
        service_id = registry_arn.rsplit("/", 1)[-1]
        try:
            response = sd_client.get_service(Id=service_id)
            svc = response.get("Service", {})
            discovered.append(
                {
                    "type": NODE_CLOUD_MAP,
                    "name": svc.get("Name", service_id),
                    "namespace_id": svc.get("NamespaceId"),
                    "dns_record": svc.get("DnsConfig", {})
                    .get("DnsRecords", [{}])[0]
                    .get("Type"),
                }
            )
        except Exception:
            discovered.append(
                {
                    "type": NODE_CLOUD_MAP,
                    "name": registry.get("containerName", service_id),
                    "namespace_id": None,
                }
            )

    service_connect = service.get("serviceConnectConfiguration") or {}
    if service_connect.get("enabled"):
        for sc_service in service_connect.get("services", []):
            discovered.append(
                {
                    "type": NODE_CLOUD_MAP,
                    "name": sc_service.get("discoveryName")
                    or sc_service.get("portName", "service-connect"),
                    "namespace_id": "service-connect",
                }
            )

    return discovered


def _classify_backend_value(value: str) -> Optional[Dict[str, str]]:
    if not value:
        return None

    for pattern, node_type, label_prefix in BACKEND_PATTERNS:
        if pattern.search(value):
            host = value.split("://")[-1].split("/")[0].split(":")[0]
            short = host.split(".")[0] if "." in host else host[:32]
            return {
                "type": node_type,
                "label": f"{label_prefix}: {short}",
                "host": host,
                "source": "inferred",
            }

    return None


def infer_backends_from_task_definition(
    task_definition: Dict[str, Any],
) -> List[Dict[str, Any]]:
    backends: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for container in task_definition.get("containerDefinitions", []):
        for env in container.get("environment", []):
            name = env.get("name", "")
            value = env.get("value", "")
            if name in DB_HOST_KEYS or any(
                key in name.upper() for key in ("DB_", "DATABASE", "REDIS", "POSTGRES", "MYSQL")
            ):
                backend = _classify_backend_value(value)
                if not backend:
                    backend = {
                        "type": NODE_GENERIC,
                        "label": f"{name} (inferred)",
                        "host": value[:64],
                        "source": "env",
                    }
                key = backend.get("host") or backend["label"]
                if key not in seen:
                    seen.add(key)
                    backends.append(backend)

        for secret in container.get("secrets", []):
            secret_name = secret.get("name", "")
            if any(token in secret_name.upper() for token in ("DB", "DATABASE", "RDS", "REDIS")):
                label = f"{secret_name} (secret)"
                if label not in seen:
                    seen.add(label)
                    backends.append(
                        {
                            "type": NODE_GENERIC,
                            "label": label,
                            "host": secret.get("valueFrom", "")[:80],
                            "source": "secret",
                        }
                    )

    return backends


def _extract_ecr_from_images(container_images: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    repos: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for item in container_images:
        image = item.get("image", "")
        if ".dkr.ecr." not in image and "amazonaws.com" not in image:
            continue
        repo = image.split("@")[0].split(":")[0]
        repo_name = repo.rsplit("/", 1)[-1]
        if repo_name not in seen:
            seen.add(repo_name)
            repos.append({"type": NODE_ECR, "label": repo_name, "image": image})

    return repos


def build_topology(
    *,
    service_name: str,
    cluster_name: str,
    service: Dict[str, Any],
    task_definition: Optional[Dict[str, Any]],
    container_images: Optional[List[Dict[str, str]]],
    load_balancers: List[Dict[str, Any]],
    service_registries: List[Dict[str, Any]],
    route53_index: Dict[str, List[str]],
) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []
    notes: List[str] = []

    ecs_id = _node_id("ecs", service_name)
    nodes.append(
        {
            "id": ecs_id,
            "type": NODE_ECS,
            "label": service_name,
            "detail": cluster_name,
        }
    )

    upstream_id: Optional[str] = None

    for load_balancer in load_balancers:
        lb_id = _node_id(load_balancer["type"], load_balancer["name"])
        nodes.append(
            {
                "id": lb_id,
                "type": load_balancer["type"],
                "label": load_balancer["name"],
                "detail": f"{load_balancer['lb_type'].upper()} · {load_balancer['scheme']}",
            }
        )
        edges.append({"from": lb_id, "to": ecs_id, "label": "routes traffic"})

        dns_name = load_balancer.get("dns_name", "")
        record_names = route53_index.get(dns_name, []) if dns_name else []
        if record_names:
            for record_name in record_names[:3]:
                r53_id = _node_id("r53", record_name)
                nodes.append(
                    {
                        "id": r53_id,
                        "type": NODE_ROUTE53,
                        "label": record_name,
                        "detail": "DNS record",
                    }
                )
                edges.append({"from": r53_id, "to": lb_id, "label": "alias"})
            upstream_id = _node_id("r53", record_names[0])
        else:
            upstream_id = lb_id
            if load_balancer.get("scheme") == "internet-facing":
                internet_id = _node_id("internet", service_name)
                nodes.append(
                    {
                        "id": internet_id,
                        "type": NODE_INTERNET,
                        "label": "Internet",
                        "detail": "public load balancer",
                    }
                )
                edges.append({"from": internet_id, "to": lb_id, "label": "HTTPS/HTTP"})

    for registry in service_registries:
        reg_id = _node_id("sd", registry["name"])
        nodes.append(
            {
                "id": reg_id,
                "type": NODE_CLOUD_MAP,
                "label": registry["name"],
                "detail": "Cloud Map / Service Connect",
            }
        )
        edges.append({"from": reg_id, "to": ecs_id, "label": "discovers"})

    if not load_balancers and not service_registries:
        notes.append("No load balancer or service registry attached to this ECS service.")

    backends: List[Dict[str, Any]] = []
    if task_definition:
        backends.extend(infer_backends_from_task_definition(task_definition))

    for backend in backends:
        backend_id = _node_id(backend["type"], backend["label"])
        nodes.append(
            {
                "id": backend_id,
                "type": backend["type"],
                "label": backend["label"],
                "detail": f"{backend.get('source', 'inferred')} backend",
            }
        )
        edges.append({"from": ecs_id, "to": backend_id, "label": "connects"})

    if container_images:
        for repo in _extract_ecr_from_images(container_images):
            ecr_id = _node_id("ecr", repo["label"])
            nodes.append(
                {
                    "id": ecr_id,
                    "type": NODE_ECR,
                    "label": repo["label"],
                    "detail": "container image",
                }
            )
            edges.append({"from": ecr_id, "to": ecs_id, "label": "pulls image"})

    if upstream_id:
        entry = upstream_id
    elif load_balancers:
        entry = _node_id(load_balancers[0]["type"], load_balancers[0]["name"])
    else:
        entry = ecs_id

    summary_parts: List[str] = []
    if any(node["type"] == NODE_ROUTE53 for node in nodes):
        summary_parts.append("Route 53")
    if any(node["type"] in {NODE_ALB, NODE_NLB} for node in nodes):
        summary_parts.append("Load Balancer")
    summary_parts.append("ECS")
    if backends:
        summary_parts.append("backend(s)")
    if any(node["type"] == NODE_ECR for node in nodes):
        summary_parts.append("ECR")

    return {
        "status": "PASS",
        "summary": " → ".join(summary_parts) if summary_parts else "ECS only",
        "entrypoint": entry,
        "nodes": nodes,
        "edges": edges,
        "notes": notes,
        "mermaid": build_mermaid(nodes, edges),
    }


def build_mermaid(nodes: List[Dict[str, Any]], edges: List[Dict[str, str]]) -> str:
    if not nodes:
        return "flowchart LR\n  ecs[ECS Service]"

    type_shape = {
        NODE_ROUTE53: ("([", "])"),
        NODE_ALB: ("[[", "]]"),
        NODE_NLB: ("[[", "]]"),
        NODE_ECS: ("[", "]"),
        NODE_RDS: ("[(", ")]"),
        NODE_REDIS: ("[(", ")]"),
        NODE_DOCDB: ("[(", ")]"),
        NODE_DYNAMODB: ("[(", ")]"),
        NODE_CLOUD_MAP: ("{{", "}}"),
        NODE_ECR: (">", "]"),
        NODE_INTERNET: ("((", "))"),
    }

    lines = ["flowchart LR"]
    for node in nodes:
        left, right = type_shape.get(node["type"], ("[", "]"))
        label = node["label"].replace('"', "'")
        lines.append(f'  {node["id"]}{left}{label}{right}')

    for edge in edges:
        edge_label = edge.get("label")
        if edge_label:
            lines.append(f'  {edge["from"]} -->|{edge_label}| {edge["to"]}')
        else:
            lines.append(f'  {edge["from"]} --> {edge["to"]}')

    return "\n".join(lines)


def discover_connectivity(
    *,
    elbv2_client,
    sd_client,
    service: Dict[str, Any],
    task_definition: Optional[Dict[str, Any]],
    container_images: Optional[List[Dict[str, str]]],
    service_name: str,
    cluster_name: str,
    route53_index: Dict[str, List[str]],
) -> Dict[str, Any]:
    load_balancers = discover_load_balancers(elbv2_client, service)
    service_registries = discover_service_registries(sd_client, service)

    return build_topology(
        service_name=service_name,
        cluster_name=cluster_name,
        service=service,
        task_definition=task_definition,
        container_images=container_images,
        load_balancers=load_balancers,
        service_registries=service_registries,
        route53_index=route53_index,
    )
