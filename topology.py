"""Discover rough ECS connectivity (LB, Route 53, backends) and build diagrams."""

from __future__ import annotations

import fnmatch
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
NODE_BEDROCK = "bedrock"
NODE_S3 = "s3"
NODE_SQS = "sqs"
NODE_SNS = "sns"
NODE_OPENSEARCH = "opensearch"
NODE_MSK = "msk"
NODE_CLOUD_MAP = "cloud_map"
NODE_ECR = "ecr"
NODE_INTERNET = "internet"
NODE_GENERIC = "backend"
NODE_TARGET_GROUP = "target_group"

BACKEND_NODE_TYPES = {
    NODE_RDS,
    NODE_REDIS,
    NODE_DOCDB,
    NODE_DYNAMODB,
    NODE_BEDROCK,
    NODE_S3,
    NODE_SQS,
    NODE_SNS,
    NODE_OPENSEARCH,
    NODE_MSK,
    NODE_GENERIC,
}

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
    "DYNAMODB_TABLE",
    "TABLE_NAME",
    "BEDROCK_ENDPOINT",
    "BEDROCK_MODEL_ID",
    "BEDROCK_MODEL",
    "AWS_BEDROCK_MODEL_ID",
    "S3_BUCKET",
    "S3_BUCKET_NAME",
    "BUCKET_NAME",
    "SQS_QUEUE_URL",
    "QUEUE_URL",
    "SNS_TOPIC_ARN",
}

NAME_HINTS = (
    "DATABASE",
    "POSTGRES",
    "MYSQL",
    "MARIA",
    "MONGO",
    "REDIS",
    "ELASTICACHE",
    "DOCDB",
    "AURORA",
    "RDS_",
    "DB_",
    "DYNAMO",
    "TABLE_NAME",
    "BEDROCK",
    "CLAUDE",
    "TITAN",
    "NOVA_",
    "FOUNDATION_MODEL",
    "S3_",
    "BUCKET",
    "SQS",
    "QUEUE_URL",
    "SNS_TOPIC",
    "OPENSEARCH",
    "ELASTICSEARCH",
    "KAFKA",
    "MSK_",
    "BROKER",
    "REDSHIFT",
)

SKIP_ENV_NAMES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_EXECUTION_ENV",
    "PATH",
    "HOME",
    "LANG",
    "HOSTNAME",
}

BACKEND_PATTERNS: List[Tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\.rds\.amazonaws\.com", re.I), NODE_RDS, "RDS"),
    (re.compile(r"\.cache\.amazonaws\.com", re.I), NODE_REDIS, "ElastiCache"),
    (re.compile(r"\.docdb\.amazonaws\.com", re.I), NODE_DOCDB, "DocumentDB"),
    (re.compile(r"bedrock-runtime\.|bedrock\.[a-z0-9-]+\.amazonaws", re.I), NODE_BEDROCK, "Bedrock"),
    (re.compile(r"amazon\.bedrock|anthropic\.claude|amazon\.(titan|nova)|meta\.llama|cohere\.|mistral\.", re.I), NODE_BEDROCK, "Bedrock"),
    (re.compile(r"\.dynamodb\.", re.I), NODE_DYNAMODB, "DynamoDB"),
    (re.compile(r"\.docdb-elastic\.|\.docdb\.", re.I), NODE_DOCDB, "DocumentDB"),
    (re.compile(r"s3[.-][a-z0-9-]+\.amazonaws\.com|^s3://", re.I), NODE_S3, "S3"),
    (re.compile(r"\.sqs\.", re.I), NODE_SQS, "SQS"),
    (re.compile(r"\.sns\.", re.I), NODE_SNS, "SNS"),
    (re.compile(r"\.es\.amazonaws\.com|\.aoss\.amazonaws|\.opensearch\.", re.I), NODE_OPENSEARCH, "OpenSearch"),
    (re.compile(r"kafka\.[a-z0-9-]+\.amazonaws|\.amazonaws\.com:909", re.I), NODE_MSK, "MSK"),
    (re.compile(r"^postgres(ql)?://|^mysql://|^mariadb://", re.I), NODE_RDS, "Database"),
    (re.compile(r"^mongodb(\+srv)?://", re.I), NODE_DOCDB, "Mongo"),
    (re.compile(r"^rediss?://", re.I), NODE_REDIS, "Redis"),
]

ARN_PATTERNS: List[Tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"arn:aws:dynamodb:[^:]+:[^:]+:table/([^/\s]+)", re.I), NODE_DYNAMODB, "DynamoDB"),
    (re.compile(r"arn:aws:rds:[^:]+:[^:]+:db:([^\s]+)", re.I), NODE_RDS, "RDS"),
    (re.compile(r"arn:aws:rds:[^:]+:[^:]+:cluster:([^\s]+)", re.I), NODE_RDS, "Aurora"),
    (re.compile(r"arn:aws:s3:::([^\s/]+)", re.I), NODE_S3, "S3"),
    (re.compile(r"arn:aws:sqs:[^:]+:[^:]+:([^\s]+)", re.I), NODE_SQS, "SQS"),
    (re.compile(r"arn:aws:sns:[^:]+:[^:]+:([^\s]+)", re.I), NODE_SNS, "SNS"),
    (re.compile(r"arn:aws:bedrock:[^:]+:[^:]*:(foundation-model|inference-profile)/([^\s]+)", re.I), NODE_BEDROCK, "Bedrock"),
    (re.compile(r"arn:aws:es:[^:]+:[^:]+:domain/([^\s]+)", re.I), NODE_OPENSEARCH, "OpenSearch"),
    (re.compile(r"arn:aws:kafka:[^:]+:[^:]+:cluster/([^/\s]+)", re.I), NODE_MSK, "MSK"),
    (re.compile(r"arn:aws:elasticache:", re.I), NODE_REDIS, "ElastiCache"),
    (re.compile(r"arn:aws:docdb:", re.I), NODE_DOCDB, "DocumentDB"),
]


def _node_id(prefix: str, label: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", label.lower())[:40]
    return f"{prefix}_{safe}"


ROUTE53_INDEX_TYPES = {"A", "AAAA", "CNAME"}
ELB_IDENTITY_RE = re.compile(
    r"^(?:internal-)?(.+?)\.(?:[a-z0-9-]+\.)?elb(?:\.[a-z0-9-]+)?\.amazonaws\.com(?:\.cn)?$",
    re.I,
)


def normalize_hostname(name: str) -> str:
    return (name or "").strip().rstrip(".").lower()


def normalize_elb_dns(name: str) -> str:
    """ALB DNS and Route 53 alias targets differ by trailing dots and dualstack."""
    host = normalize_hostname(name)
    changed = True
    while changed:
        changed = False
        for prefix in ("dualstack.", "ipv6."):
            if host.startswith(prefix):
                host = host[len(prefix) :]
                changed = True
    return host


def _elb_identity(dns_name: str) -> str:
    match = ELB_IDENTITY_RE.match(normalize_elb_dns(dns_name))
    return match.group(1).lower() if match else ""


def _targets_same_elb(target: str, lb_dns: str) -> bool:
    left = normalize_elb_dns(target)
    right = normalize_elb_dns(lb_dns)
    if not left or not right:
        return False
    if left == right:
        return True
    left_id = _elb_identity(left)
    right_id = _elb_identity(right)
    return bool(left_id and right_id and left_id == right_id)


def empty_route53_catalog(
    errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "by_target": {},
        "by_name": {},
        "wildcards": {},
        "stats": {"zones": 0, "records": 0},
        "errors": list(errors or []),
    }


def _as_route53_catalog(route53_index: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not route53_index:
        return empty_route53_catalog()
    if "by_target" in route53_index:
        return route53_index
    return {
        "by_target": route53_index,
        "by_name": {},
        "wildcards": {},
        "stats": {"zones": 0, "records": 0},
        "errors": [],
    }


def route53_report_summary(route53_index: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    catalog = _as_route53_catalog(route53_index)
    stats = catalog.get("stats") or {}
    return {
        "zones_scanned": int(stats.get("zones") or 0),
        "records_scanned": int(stats.get("records") or 0),
        "errors": list(catalog.get("errors") or [])[:12],
    }


def lookup_route53_records(
    route53_index: Dict[str, List[Any]],
    dns_name: str,
    extra_hosts: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    catalog = _as_route53_catalog(route53_index)
    by_target: Dict[str, List[Any]] = catalog.get("by_target") or {}
    by_name: Dict[str, List[Any]] = catalog.get("by_name") or {}
    wildcards: Dict[str, List[Any]] = catalog.get("wildcards") or {}
    records: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, Optional[str]]] = set()

    def add_item(item: Any, fallback_target: Optional[str] = None) -> None:
        if isinstance(item, str):
            record = {
                "name": item.rstrip("."),
                "type": "A",
                "alias": True,
                "target": fallback_target or dns_name,
            }
        else:
            record = dict(item)
            if record.get("name"):
                record["name"] = str(record["name"]).rstrip(".")
        name = record.get("name")
        marker = (str(name), record.get("type"))
        if not name or marker in seen:
            return
        seen.add(marker)
        records.append(record)

    lb_dns = normalize_elb_dns(dns_name or "")
    if lb_dns:
        for target_key, items in by_target.items():
            if _targets_same_elb(str(target_key), lb_dns):
                for item in items:
                    add_item(item, dns_name)

    for extra in extra_hosts or []:
        host = normalize_hostname(str(extra))
        if not host or "*" in host:
            continue
        for item in by_name.get(host) or []:
            add_item(item)
        for pattern, items in wildcards.items():
            if fnmatch.fnmatch(host, pattern):
                for item in items:
                    add_item(item)

    found_names = {normalize_hostname(str(item.get("name"))) for item in records}
    hops = 0
    changed = True
    while changed and hops < 3:
        changed = False
        hops += 1
        for name in list(found_names):
            if not name:
                continue
            chained = list(by_target.get(name) or [])
            chained.extend(by_target.get(normalize_elb_dns(name)) or [])
            before = len(records)
            for item in chained:
                add_item(item)
            if len(records) > before:
                found_names = {
                    normalize_hostname(str(item.get("name"))) for item in records
                }
                changed = True

    return records


def build_route53_index(route53_client) -> Dict[str, Any]:
    """Index Route 53 A/AAAA/CNAME records by target and by name."""
    catalog = empty_route53_catalog()
    by_target: Dict[str, List[Dict[str, Any]]] = catalog["by_target"]
    by_name: Dict[str, List[Dict[str, Any]]] = catalog["by_name"]
    wildcards: Dict[str, List[Dict[str, Any]]] = catalog["wildcards"]
    errors: List[str] = catalog["errors"]
    zones = 0
    record_count = 0

    try:
        zone_paginator = route53_client.get_paginator("list_hosted_zones")
        zone_pages = zone_paginator.paginate()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ListHostedZones failed: {exc}")
        return catalog

    for zone_page in zone_pages:
        for zone in zone_page.get("HostedZones", []):
            zone_id = zone["Id"].split("/")[-1]
            zone_name = (zone.get("Name") or "").rstrip(".")
            zones += 1
            try:
                record_paginator = route53_client.get_paginator(
                    "list_resource_record_sets"
                )
                for record_page in record_paginator.paginate(HostedZoneId=zone_id):
                    for record in record_page.get("ResourceRecordSets", []):
                        record_type = str(record.get("Type") or "")
                        if record_type not in ROUTE53_INDEX_TYPES:
                            continue
                        record_name = (record.get("Name") or "").rstrip(".")
                        if not record_name:
                            continue
                        record_count += 1
                        alias = record.get("AliasTarget")
                        targets: List[Tuple[str, bool]] = []
                        if alias:
                            targets.append((alias.get("DNSName") or "", True))
                        for rr in record.get("ResourceRecords", []):
                            targets.append((rr.get("Value") or "", False))
                        name_key = normalize_hostname(record_name)
                        named = False
                        for target, is_alias in targets:
                            payload = {
                                "name": record_name,
                                "type": record_type,
                                "alias": is_alias,
                                "zone_id": zone_id,
                                "zone_name": zone_name,
                                "target": (target or "").rstrip("."),
                            }
                            if not named:
                                by_name.setdefault(name_key, []).append(payload)
                                if "*" in name_key:
                                    wildcards.setdefault(name_key, []).append(payload)
                                named = True
                            target_key = normalize_elb_dns(target)
                            if target_key:
                                by_target.setdefault(target_key, []).append(payload)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{zone_name or zone_id}: {exc}")
                continue

    catalog["stats"] = {"zones": zones, "records": record_count}
    return catalog


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


def _action_target_groups(action: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    arns: List[str] = []
    if action.get("Type") != "forward":
        return [], []
    if action.get("TargetGroupArn"):
        arns.append(action["TargetGroupArn"])
    for group in (action.get("ForwardConfig") or {}).get("TargetGroups", []):
        arn = group.get("TargetGroupArn")
        if arn:
            arns.append(arn)
    names = [
        arn.rsplit("/", 2)[-2] if "/" in arn else arn
        for arn in arns
    ]
    return arns, names


def _host_header_values(condition: Dict[str, Any]) -> List[str]:
    if (condition.get("Field") or "").lower() != "host-header":
        return []
    values = list((condition.get("HostHeaderConfig") or {}).get("Values") or [])
    values.extend(condition.get("Values") or [])
    return [str(value).rstrip(".").lower() for value in values if value]


def _describe_listener_host_rules(
    elbv2_client, listener_arn: str
) -> List[Dict[str, Any]]:
    rules: List[Dict[str, Any]] = []
    try:
        paginator = elbv2_client.get_paginator("describe_rules")
        for page in paginator.paginate(ListenerArn=listener_arn):
            for rule in page.get("Rules", []):
                if rule.get("IsDefault"):
                    continue
                hosts: List[str] = []
                for condition in rule.get("Conditions", []):
                    hosts.extend(_host_header_values(condition))
                if not hosts:
                    continue
                arns: List[str] = []
                names: List[str] = []
                action_labels: List[str] = []
                for action in rule.get("Actions", []):
                    action_arns, action_names = _action_target_groups(action)
                    arns.extend(action_arns)
                    names.extend(action_names)
                    if action.get("Type") == "forward" and action_names:
                        action_labels.append("forward → " + ", ".join(action_names))
                    elif action.get("Type"):
                        action_labels.append(str(action["Type"]))
                rules.append(
                    {
                        "priority": rule.get("Priority"),
                        "hosts": list(dict.fromkeys(hosts)),
                        "target_group_arns": list(dict.fromkeys(arns)),
                        "target_groups": list(dict.fromkeys(names)),
                        "action": " · ".join(action_labels) or "forward",
                    }
                )
    except Exception:
        return rules
    return rules


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
                        _, names = _action_target_groups(action)
                        if names:
                            actions.append("forward → " + ", ".join(names))
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
                listener_arn = listener.get("ListenerArn")
                listeners.append(
                    {
                        "arn": listener_arn,
                        "port": listener.get("Port"),
                        "protocol": listener.get("Protocol"),
                        "ssl_policy": listener.get("SslPolicy"),
                        "default_actions": actions,
                        "certificates": certificates,
                        "host_header_rules": (
                            _describe_listener_host_rules(elbv2_client, listener_arn)
                            if listener_arn
                            else []
                        ),
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


def _host_from_value(value: str) -> Tuple[str, Optional[int]]:
    text = (value or "").strip()
    if not text:
        return "", None
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/")[0].split("?")[0]
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    port: Optional[int] = None
    if text.count(":") == 1:
        host, maybe_port = text.split(":")
        if maybe_port.isdigit():
            return host, int(maybe_port)
    return text, port


def _classify_arn(value: str) -> Optional[Dict[str, Any]]:
    for pattern, node_type, label_prefix in ARN_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        ident = match.group(match.lastindex) if match.lastindex else value[:48]
        return {
            "type": node_type,
            "label": f"{label_prefix}: {ident}",
            "identifier": ident,
            "arn": value[:200],
            "source": "arn",
        }
    return None


def _classify_backend_value(value: str) -> Optional[Dict[str, Any]]:
    if not value or value.strip().lower() in {"true", "false", "yes", "no"}:
        return None
    if value.startswith("arn:"):
        return _classify_arn(value)

    for pattern, node_type, label_prefix in BACKEND_PATTERNS:
        if pattern.search(value):
            host, port = _host_from_value(value)
            short = host.split(".")[0] if "." in host else (host or value)[:48]
            payload: Dict[str, Any] = {
                "type": node_type,
                "label": f"{label_prefix}: {short}",
                "host": host,
                "source": "inferred",
            }
            if port:
                payload["port"] = port
            if node_type == NODE_RDS and host:
                payload["identifier"] = host.split(".")[0]
                payload["aurora_cluster"] = ".cluster-" in host.lower()
            if node_type == NODE_REDIS and host:
                payload["identifier"] = host.split(".")[0]
            if node_type == NODE_BEDROCK and "." in value and "amazonaws" not in value.lower():
                payload["identifier"] = value.split("/")[-1][:120]
            if node_type == NODE_S3:
                if value.lower().startswith("s3://"):
                    payload["identifier"] = value[5:].split("/")[0]
                elif host:
                    payload["identifier"] = host.split(".")[0]
            return payload
    return None


def _name_hinted(name: str) -> bool:
    upper = name.upper()
    if name in DB_HOST_KEYS:
        return True
    return any(token in upper for token in NAME_HINTS)


def _backend_from_env(name: str, value: str) -> Optional[Dict[str, Any]]:
    if not name or name.upper() in SKIP_ENV_NAMES:
        return None
    classified = _classify_backend_value(value)
    if classified:
        classified["env"] = name
        return classified
    if not _name_hinted(name) or not value:
        return None
    upper = name.upper()
    host, port = _host_from_value(value)
    backend: Dict[str, Any] = {
        "label": f"{name}",
        "host": host or value[:80],
        "source": "env",
        "env": name,
    }
    if port:
        backend["port"] = port
    if "BEDROCK" in upper or "CLAUDE" in upper or "TITAN" in upper:
        backend["type"] = NODE_BEDROCK
        backend["identifier"] = value[:120]
        backend["label"] = f"Bedrock: {value[:48]}"
    elif "DYNAMO" in upper or upper.endswith("_TABLE") or upper == "TABLE_NAME":
        backend["type"] = NODE_DYNAMODB
        backend["identifier"] = value[:255]
        backend["label"] = f"DynamoDB: {value[:48]}"
    elif "BUCKET" in upper or upper.startswith("S3_"):
        backend["type"] = NODE_S3
        backend["identifier"] = value.replace("s3://", "").split("/")[0]
        backend["label"] = f"S3: {backend['identifier'][:48]}"
    elif "SQS" in upper or "QUEUE" in upper:
        backend["type"] = NODE_SQS
        backend["identifier"] = value[:200]
        backend["label"] = f"SQS: {name}"
    elif "SNS" in upper or "TOPIC" in upper:
        backend["type"] = NODE_SNS
        backend["identifier"] = value[:200]
        backend["label"] = f"SNS: {name}"
    elif any(token in upper for token in ("REDIS", "ELASTICACHE", "CACHE")):
        backend["type"] = NODE_REDIS
        backend["identifier"] = (host or value).split(".")[0]
        backend["label"] = f"ElastiCache: {backend['identifier']}"
    elif any(token in upper for token in ("OPENSEARCH", "ELASTICSEARCH")):
        backend["type"] = NODE_OPENSEARCH
        backend["identifier"] = (host or value).split(".")[0]
        backend["label"] = f"OpenSearch: {backend['identifier']}"
    elif "KAFKA" in upper or "MSK" in upper:
        backend["type"] = NODE_MSK
        backend["label"] = f"MSK: {name}"
    else:
        backend["type"] = NODE_RDS if any(
            token in upper for token in ("DB", "DATABASE", "POSTGRES", "MYSQL", "RDS", "AURORA")
        ) else NODE_GENERIC
        if host and ".rds." in host:
            backend["type"] = NODE_RDS
            backend["identifier"] = host.split(".")[0]
        backend["label"] = f"{backend['type'].upper()}: {name}"
    return backend


def infer_backends_from_task_definition(
    task_definition: Dict[str, Any],
) -> List[Dict[str, Any]]:
    backends: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def add(backend: Optional[Dict[str, Any]]) -> None:
        if not backend:
            return
        key = (
            backend.get("arn")
            or backend.get("identifier")
            or backend.get("host")
            or backend.get("label")
        )
        if not key or key in seen:
            return
        seen.add(str(key))
        backends.append(backend)

    for container in task_definition.get("containerDefinitions", []) if task_definition else []:
        for env in container.get("environment", []):
            add(_backend_from_env(env.get("name", ""), env.get("value", "")))

        for secret in container.get("secrets", []):
            secret_name = secret.get("name", "")
            value_from = str(secret.get("valueFrom") or "")
            classified = _classify_arn(value_from) if value_from.startswith("arn:") else None
            if classified:
                classified["env"] = secret_name
                classified["source"] = "secret"
                add(classified)
                continue
            if not _name_hinted(secret_name):
                continue
            add(
                {
                    "type": NODE_GENERIC,
                    "label": f"{secret_name} (secret)",
                    "host": value_from[:120],
                    "source": "secret",
                    "env": secret_name,
                    "probe": "skip",
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
    route53_index: Dict[str, List[Any]],
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
        extra_hosts: List[str] = []
        for listener in load_balancer.get("listeners") or []:
            for rule in listener.get("host_header_rules") or []:
                extra_hosts.extend(rule.get("hosts") or [])
        records = lookup_route53_records(
            route53_index,
            dns_name,
            extra_hosts=extra_hosts,
        )
        load_balancer["dns_records"] = records
        if records:
            for record in records:
                record_name = record["name"]
                r53_id = _node_id("r53", record_name)
                record_type = record.get("type") or "A"
                detail = (
                    f"Alias {record_type}"
                    if record.get("alias")
                    else str(record_type)
                )
                if record.get("zone_name"):
                    detail += f" · {record['zone_name']}"
                nodes.append(
                    {
                        "id": r53_id,
                        "type": NODE_ROUTE53,
                        "label": record_name,
                        "detail": detail,
                    }
                )
                edges.append({"from": r53_id, "to": lb_id, "label": "alias"})
            upstream_id = _node_id("r53", records[0]["name"])
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
    dns_records: List[Dict[str, Any]] = []
    for node in nodes:
        if node.get("type") == NODE_ROUTE53 and node.get("label"):
            http_hosts.append(node["label"])
    for load_balancer in load_balancers:
        dns_name = load_balancer.get("dns_name")
        if dns_name and dns_name not in http_hosts:
            http_hosts.append(dns_name)
        for record in load_balancer.get("dns_records") or []:
            dns_records.append(
                {
                    **record,
                    "load_balancer": load_balancer.get("name"),
                    "load_balancer_dns": dns_name,
                }
            )

    return {
        "status": "PASS",
        "summary": " → ".join(summary_parts) if summary_parts else "ECS only",
        "entrypoint": entry,
        "nodes": nodes,
        "edges": edges,
        "notes": notes,
        "http_hosts": http_hosts,
        "dns_records": dns_records,
        "load_balancers": load_balancers,
        "backends": backends,
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
        NODE_BEDROCK: ("[[", "]]"),
        NODE_S3: ("[(", ")]"),
        NODE_SQS: ("[", "]"),
        NODE_SNS: ("[", "]"),
        NODE_OPENSEARCH: ("[(", ")]"),
        NODE_MSK: ("[(", ")]"),
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
    route53_index: Dict[str, List[Any]],
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
