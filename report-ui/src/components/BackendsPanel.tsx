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

interface Props {
  backends: ClusterBackend[];
}

export function BackendsPanel({ backends }: Props) {
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

  const issues = backends.filter(
    (item) => item.status === "FAIL" || item.status === "WARN",
  ).length;

  return (
    <div className="target-groups">
      <div className="target-groups-head">
        <h4>Backends</h4>
        <span className="target-groups-summary">
          {backends.length} detected
          {issues ? ` · ${issues} need attention` : ""}
        </span>
      </div>
      <ul className="target-group-list">
        {backends.map((backend) => (
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
                label={TYPE_LABELS[backend.type ?? ""] ?? backend.type ?? "Backend"}
              />
            </div>
            <div className="target-group-meta">
              <span>Service {backend.service}</span>
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
    </div>
  );
}
