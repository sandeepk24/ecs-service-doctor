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
NODE_TARGET_GROUP = "target_group"

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


def discover_load_balancers(
    elbv2_client, service: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    attachments_by_arn = {
        item["targetGroupArn"]: item
        for item in service.get("loadBalancers", [])
        if item.get("targetGroupArn")
    }
    target_group_arns = list(attachments_by_arn.keys())
    if not target_group_arns:
        return [], []

    load_balancers: Dict[str, Dict[str, Any]] = {}
    target_groups: List[Dict[str, Any]] = []

    for offset in range(0, len(target_group_arns), 20):
        batch = target_group_arns[offset : offset + 20]
        response = elbv2_client.describe_target_groups(TargetGroupArns=batch)
        lb_arns: Set[str] = set()

        for target_group in response.get("TargetGroups", []):
            tg_arn = target_group["TargetGroupArn"]
            tg_name = target_group.get("TargetGroupName", "target-group")
            attachment = attachments_by_arn.get(tg_arn, {})
            target_groups.append(
                {
                    "arn": tg_arn,
                    "name": tg_name,
                    "port": target_group.get("Port"),
                    "protocol": target_group.get("Protocol"),
                    "load_balancer_arns": target_group.get("LoadBalancerArns", []),
                    "container_name": attachment.get("containerName"),
                    "container_port": attachment.get("containerPort"),
                }
            )
            for lb_arn in target_group.get("LoadBalancerArns", []):
                lb_arns.add(lb_arn)

        if not lb_arns:
            continue

        lb_response = elbv2_client.describe_load_balancers(
            LoadBalancerArns=list(lb_arns)
        )
        for load_balancer in lb_response.get("LoadBalancers", []):
            arn = load_balancer["LoadBalancerArn"]
            lb_type = load_balancer.get("Type", "application")
            zones = [
                zone.get("ZoneName")
                for zone in load_balancer.get("AvailabilityZones", [])
                if zone.get("ZoneName")
            ]
            subnets = [
                zone.get("SubnetId")
                for zone in load_balancer.get("AvailabilityZones", [])
                if zone.get("SubnetId")
            ]
            listeners = _describe_listeners(elbv2_client, arn)
            load_balancers[arn] = {
                "arn": arn,
                "type": NODE_ALB if lb_type == "application" else NODE_NLB,
                "lb_type": lb_type,
                "name": load_balancer.get("LoadBalancerName", "load-balancer"),
                "dns_name": load_balancer.get("DNSName", "").rstrip("."),
                "hosted_zone_id": load_balancer.get("CanonicalHostedZoneId"),
                "scheme": load_balancer.get("Scheme", "unknown"),
                "state": (load_balancer.get("State") or {}).get("Code", "unknown"),
                "vpc_id": load_balancer.get("VpcId"),
                "ip_address_type": load_balancer.get("IpAddressType"),
                "availability_zones": zones,
                "subnets": subnets,
                "security_groups": load_balancer.get("SecurityGroups", []),
                "created_at": str(load_balancer.get("CreatedTime", "")),
                "listeners": listeners,
            }

    return list(load_balancers.values()), target_groups


def _describe_listeners(elbv2_client, load_balancer_arn: str) -> List[Dict[str, Any]]:
    listeners: List[Dict[str, Any]] = []
    try:
        paginator = elbv2_client.get_paginator("describe_listeners")
        for page in paginator.paginate(LoadBalancerArn=load_balancer_arn):
            for listener in page.get("Listeners", []):
                actions = []
                for action in listener.get("DefaultActions", []):
                    action_type = action.get("Type", "unknown")
                    target_group_arn = action.get("TargetGroupArn")
                    if action_type == "forward":
                        groups = (action.get("ForwardConfig") or {}).get(
                            "TargetGroups", []
                        )
                        if groups:
                            names = [
                                group.get("TargetGroupArn", "").rsplit("/", 2)[-2]
                                if "/" in group.get("TargetGroupArn", "")
                                else group.get("TargetGroupArn")
                                for group in groups
                            ]
                            actions.append("forward → " + ", ".join(filter(None, names)))
                        elif target_group_arn:
                            actions.append(
                                "forward → " + target_group_arn.rsplit("/", 2)[-2]
                            )
                        else:
                            actions.append("forward")
                    elif action_type == "redirect":
                        redirect = action.get("RedirectConfig") or {}
                        actions.append(
                            "redirect → "
                            f"{redirect.get('Protocol', 'HTTPS')}:"
                            f"{redirect.get('Port', '443')}"
                        )
                    elif action_type == "fixed-response":
                        actions.append(
                            "fixed-response "
                            f"{(action.get('FixedResponseConfig') or {}).get('StatusCode', '')}"
                        )
                    else:
                        actions.append(action_type)
                certificates = [
                    cert.get("CertificateArn", "").split("/")[-1]
                    for cert in listener.get("Certificates", [])
                    if cert.get("CertificateArn")
                ]
                listeners.append(
                    {
                        "port": listener.get("Port"),
                        "protocol": listener.get("Protocol"),
                        "ssl_policy": listener.get("SslPolicy"),
                        "default_actions": actions,
                        "certificates": certificates,
                    }
                )
    except Exception:
        return listeners
    return listeners


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
    target_groups: List[Dict[str, Any]],
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
        listener_bits = []
        for listener in load_balancer.get("listeners") or []:
            protocol = listener.get("protocol") or "TCP"
            port = listener.get("port")
            if port is not None:
                listener_bits.append(f"{protocol}:{port}")
        az_bits = load_balancer.get("availability_zones") or []
        detail_parts = [
            (load_balancer.get("lb_type") or "application").upper(),
            load_balancer.get("scheme") or "unknown",
        ]
        if load_balancer.get("state"):
            detail_parts.append(str(load_balancer["state"]))
        if listener_bits:
            detail_parts.append(", ".join(listener_bits))

        nodes.append(
            {
                "id": lb_id,
                "type": load_balancer["type"],
                "label": load_balancer["name"],
                "detail": " · ".join(detail_parts),
                "dns_name": load_balancer.get("dns_name"),
                "vpc_id": load_balancer.get("vpc_id"),
                "availability_zones": az_bits,
            }
        )

        lb_target_groups = [
            target_group
            for target_group in target_groups
            if load_balancer["arn"] in target_group.get("load_balancer_arns", [])
        ]
        if not lb_target_groups:
            lb_target_groups = target_groups

        for target_group in lb_target_groups:
            tg_id = _node_id("tg", target_group["name"])
            port_bits = []
            if target_group.get("protocol"):
                port_bits.append(str(target_group["protocol"]))
            if target_group.get("port") is not None:
                port_bits.append(str(target_group["port"]))
            detail = " · ".join(port_bits) if port_bits else "target group"
            if target_group.get("container_name"):
                detail += f" → {target_group['container_name']}"
                if target_group.get("container_port") is not None:
                    detail += f":{target_group['container_port']}"

            nodes.append(
                {
                    "id": tg_id,
                    "type": NODE_TARGET_GROUP,
                    "label": target_group["name"],
                    "detail": detail,
                }
            )
            edges.append({"from": lb_id, "to": tg_id, "label": "forwards to"})
            edges.append({"from": tg_id, "to": ecs_id, "label": "registers tasks"})

        if not lb_target_groups:
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
    if any(node["type"] == NODE_TARGET_GROUP for node in nodes):
        summary_parts.append("Target Group")
    summary_parts.append("ECS")
    if backends:
        summary_parts.append("backend(s)")
    if any(node["type"] == NODE_ECR for node in nodes):
        summary_parts.append("ECR")

    http_hosts: List[str] = []
    for node in nodes:
        if node.get("type") == NODE_ROUTE53 and node.get("label"):
            http_hosts.append(node["label"])
    for load_balancer in load_balancers:
        dns_name = load_balancer.get("dns_name")
        if dns_name and dns_name not in http_hosts:
            http_hosts.append(dns_name)

    return {
        "status": "PASS",
        "summary": " → ".join(summary_parts) if summary_parts else "ECS only",
        "entrypoint": entry,
        "nodes": nodes,
        "edges": edges,
        "notes": notes,
        "http_hosts": http_hosts,
        "load_balancers": load_balancers,
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
        NODE_TARGET_GROUP: ("/", "\\"),
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


def collect_peer_hints(
    task_definition: Optional[Dict[str, Any]],
    connectivity: Optional[Dict[str, Any]],
) -> List[str]:
    """Collect strings that may name another ECS service (env, DNS, Cloud Map)."""
    hints: List[str] = []
    if connectivity:
        hints.extend(connectivity.get("http_hosts") or [])
        for node in connectivity.get("nodes", []):
            if node.get("type") in {NODE_ROUTE53, NODE_CLOUD_MAP, NODE_ALB, NODE_NLB}:
                if node.get("label"):
                    hints.append(str(node["label"]))

    if task_definition:
        for container in task_definition.get("containerDefinitions", []):
            for env in container.get("environment", []):
                if env.get("name"):
                    hints.append(str(env["name"]))
                if env.get("value"):
                    hints.append(str(env["value"]))
            for secret in container.get("secrets", []):
                if secret.get("name"):
                    hints.append(str(secret["name"]))

    return [hint for hint in hints if hint]


def service_light(item: Dict[str, Any]) -> str:
    """Green when the app is up (HTTP 200 / running). Red when not 200 or down."""
    http = (item.get("checks") or {}).get("http_health") or {}
    if http:
        expected = http.get("expected_status", 200)
        status_code = http.get("http_status")
        if http.get("status") == "PASS" and (
            status_code is None or status_code == expected
        ):
            return "green"
        return "red"

    running = ((item.get("checks") or {}).get("task_counts") or {}).get("running", 0)
    if item.get("status") == "FAIL" or not running:
        return "red"
    return "green"


def _service_aliases(item: Dict[str, Any]) -> Set[str]:
    aliases: Set[str] = set()
    name = item.get("service", "")
    aliases.add(name.lower())
    aliases.add(name.lower().replace("_", "-"))
    aliases.add(re.sub(r"[^a-z0-9]", "", name.lower()))

    connectivity = (item.get("checks") or {}).get("connectivity") or {}
    for host in connectivity.get("http_hosts") or []:
        lowered = str(host).lower().rstrip(".")
        aliases.add(lowered)
        aliases.add(lowered.split(".")[0])

    for node in connectivity.get("nodes") or []:
        if node.get("type") in {NODE_ROUTE53, NODE_CLOUD_MAP}:
            label = str(node.get("label") or "").lower().rstrip(".")
            if label:
                aliases.add(label)
                aliases.add(label.split(".")[0])

    return {alias for alias in aliases if len(alias) >= 4}


def _hint_matches_alias(hint: str, alias: str) -> bool:
    lowered = hint.lower()
    alias = alias.lower()
    compact_alias = re.sub(r"[^a-z0-9]", "", alias)
    if len(compact_alias) < 4:
        return False

    if "." in alias:
        hosts = re.findall(r"(?:https?://)?([a-z0-9.-]+\.[a-z0-9.-]+)", lowered)
        return any(
            host == alias or host.startswith(f"{alias}.") for host in hosts
        )

    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    if alias in tokens or compact_alias in tokens:
        return True
    if f"{alias}." in lowered or f"//{alias}" in lowered:
        return True
    compact_hint = "".join(tokens)
    return len(compact_alias) >= 6 and compact_alias in compact_hint


def build_service_mesh(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Cluster-level map of services, status lights, and who talks to whom."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    seen_edges: Set[Tuple[str, str]] = set()

    for item in results:
        http = (item.get("checks") or {}).get("http_health") or {}
        node_id = f"{item.get('cluster')}::{item.get('service')}"
        light = service_light(item)
        nodes.append(
            {
                "id": node_id,
                "cluster": item.get("cluster"),
                "service": item.get("service"),
                "status": item.get("status"),
                "critical": item.get("critical", False),
                "light": light,
                "http_status": http.get("http_status"),
                "http_url": http.get("url"),
            }
        )

    aliases_by_id = {
        f"{item.get('cluster')}::{item.get('service')}": _service_aliases(item)
        for item in results
    }

    for source in results:
        source_id = f"{source.get('cluster')}::{source.get('service')}"
        hints = [str(hint) for hint in source.get("peer_hints") or []]
        connectivity = (source.get("checks") or {}).get("connectivity") or {}
        for node in connectivity.get("nodes") or []:
            if node.get("type") == NODE_CLOUD_MAP and node.get("label"):
                hints.append(str(node["label"]))

        for target in results:
            target_id = f"{target.get('cluster')}::{target.get('service')}"
            if source_id == target_id:
                continue
            if (source_id, target_id) in seen_edges:
                continue
            if source.get("cluster") != target.get("cluster"):
                continue

            matched_alias = next(
                (
                    alias
                    for alias in aliases_by_id.get(target_id, set())
                    for hint in hints
                    if _hint_matches_alias(hint, alias)
                    and alias not in aliases_by_id.get(source_id, set())
                ),
                None,
            )
            if not matched_alias:
                continue

            seen_edges.add((source_id, target_id))
            target_light = service_light(target)
            source_light = service_light(source)
            edges.append(
                {
                    "from": source_id,
                    "to": target_id,
                    "from_service": source.get("service"),
                    "to_service": target.get("service"),
                    "via": "DNS / env / Service Connect",
                    "ok": source_light == "green" and target_light == "green",
                }
            )

    green = sum(1 for node in nodes if node["light"] == "green")
    red = len(nodes) - green
    blocked = sum(1 for edge in edges if not edge["ok"])
    if not nodes:
        summary = "No services to map"
    elif not edges:
        summary = f"{green} up · {red} down · no service-to-service links detected"
    elif blocked:
        summary = (
            f"{green} up · {red} down · {len(edges)} connections "
            f"({blocked} blocked because a service is not HTTP 200)"
        )
    else:
        summary = f"{green} up · {red} down · {len(edges)} connections reachable"

    return {
        "summary": summary,
        "nodes": nodes,
        "edges": edges,
    }


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
    load_balancers, target_groups = discover_load_balancers(elbv2_client, service)
    service_registries = discover_service_registries(sd_client, service)

    return build_topology(
        service_name=service_name,
        cluster_name=cluster_name,
        service=service,
        task_definition=task_definition,
        container_images=container_images,
        load_balancers=load_balancers,
        target_groups=target_groups,
        service_registries=service_registries,
        route53_index=route53_index,
    )
