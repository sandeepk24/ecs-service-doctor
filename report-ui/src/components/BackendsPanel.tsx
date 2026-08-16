import type { ReportCheck, ServiceResult, Status } from "../types";
import { StatusBadge } from "./StatusBadge";

export interface BackendItem {
  type?: string;
  label?: string;
  host?: string;
  identifier?: string;
  env?: string;
  source?: string;
  engine?: string;
  aws_status?: string;
  status?: Status;
  message?: string;
}

export interface ClusterBackend extends BackendItem {
  key: string;
  service: string;
}

const TYPE_LABELS: Record<string, string> = {
  rds: "RDS",
  redis: "ElastiCache",
  docdb: "DocumentDB",
  dynamodb: "DynamoDB",
  bedrock: "Bedrock",
  s3: "S3",
  sqs: "SQS",
  sns: "SNS",
  opensearch: "OpenSearch",
  msk: "MSK",
  backend: "Backend",
};

const BACKEND_NODE_TYPES = new Set(Object.keys(TYPE_LABELS));

const STATUS_RANK: Record<string, number> = {
  FAIL: 0,
  WARN: 1,
  PASS: 2,
};

function sortBackends(items: ClusterBackend[]): ClusterBackend[] {
  return [...items].sort((a, b) => {
    const status =
      (STATUS_RANK[a.status ?? "PASS"] ?? 9) -
      (STATUS_RANK[b.status ?? "PASS"] ?? 9);
    if (status !== 0) return status;
    const type = (a.type ?? "").localeCompare(b.type ?? "");
    if (type !== 0) return type;
    return (a.label ?? "").localeCompare(b.label ?? "");
  });
}

export function collectClusterBackends(
  services: ServiceResult[],
): ClusterBackend[] {
  const items: ClusterBackend[] = [];
  for (const item of services) {
    const check = item.checks?.backends as
      | (ReportCheck & { backends?: BackendItem[] })
      | undefined;
    const listed = check?.backends ?? [];
    if (listed.length) {
      for (const backend of listed) {
        items.push({
          ...backend,
          key: `${item.cluster}::${item.service}::${backend.label ?? backend.identifier ?? "backend"}`,
          service: item.service,
        });
      }
      continue;
    }

    const nodes = item.checks?.connectivity?.nodes ?? [];
    for (const node of nodes) {
      if (!BACKEND_NODE_TYPES.has(node.type ?? "")) continue;
      items.push({
        type: node.type,
        label: node.label,
        message: node.detail,
        status: (check?.status as Status | undefined) ?? "PASS",
        key: `${item.cluster}::${item.service}::${node.id ?? node.label}`,
        service: item.service,
      });
    }
  }
  return items;
}

function groupByService(
  backends: ClusterBackend[],
  serviceOrder: string[],
): Array<{ service: string; backends: ClusterBackend[] }> {
  const buckets = new Map<string, ClusterBackend[]>();
  for (const backend of backends) {
    const list = buckets.get(backend.service) ?? [];
    list.push(backend);
    buckets.set(backend.service, list);
  }

  const names = serviceOrder.length
    ? serviceOrder.filter((name) => buckets.has(name))
    : [...buckets.keys()];
  for (const name of buckets.keys()) {
    if (!names.includes(name)) names.push(name);
  }

  return names.map((service) => ({
    service,
    backends: sortBackends(buckets.get(service) ?? []),
  }));
}

interface Props {
  backends: ClusterBackend[];
  serviceOrder?: string[];
}

export function BackendsPanel({ backends, serviceOrder = [] }: Props) {
  if (!backends.length) {
    return (
      <div className="empty-panel">
        No backends were inferred from task-definition environment variables,
        connection strings, or ARNs. Typical signals are{" "}
        <code>DB_HOST</code>, <code>DATABASE_URL</code>,{" "}
        <code>DYNAMODB_TABLE</code>, <code>BEDROCK_MODEL_ID</code>, Redis URLs,
        and <code>s3://</code> buckets.
      </div>
    );
  }

  const groups = groupByService(backends, serviceOrder);
  const issues = backends.filter(
    (item) => item.status === "FAIL" || item.status === "WARN",
  ).length;

  return (
    <div className="backend-groups">
      <div className="target-groups-head">
        <h4>Backends</h4>
        <span className="target-groups-summary">
          {backends.length} detected · {groups.length} service
          {groups.length === 1 ? "" : "s"}
          {issues ? ` · ${issues} need attention` : ""}
        </span>
      </div>
      {groups.map((group) => (
        <section key={group.service} className="backend-service">
          <header className="backend-service-head">
            <h5>{group.service}</h5>
            <em>
              {group.backends.length} backend
              {group.backends.length === 1 ? "" : "s"}
            </em>
          </header>
          <ul className="target-group-list">
            {group.backends.map((backend) => (
              <li
                key={backend.key}
                className={`target-group-item ${
                  backend.status === "FAIL"
                    ? "issue"
                    : backend.status === "WARN"
                      ? "warn"
                      : "ok"
                }`}
              >
                <div className="target-group-title">
                  <strong>{backend.label ?? "backend"}</strong>
                  <StatusBadge
                    status={backend.status ?? "PASS"}
                    label={
                      TYPE_LABELS[backend.type ?? ""] ??
                      backend.type ??
                      "Backend"
                    }
                  />
                </div>
                <div className="target-group-meta">
                  {backend.env && <span>env {backend.env}</span>}
                  {backend.engine && <span>{backend.engine}</span>}
                  {backend.host && <span>{backend.host}</span>}
                  {backend.identifier && !backend.host && (
                    <span>{backend.identifier}</span>
                  )}
                  {backend.aws_status && <span>AWS {backend.aws_status}</span>}
                </div>
                {backend.message && (
                  <p className="backend-message">{backend.message}</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
