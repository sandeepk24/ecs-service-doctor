import type { ReportCheck, ServiceResult } from "../types";
import { StatusBadge } from "./StatusBadge";

export interface TargetGroupCheck {
  name?: string;
  target_group_arn?: string;
  attachment_ok?: boolean;
  attachment_issues?: string[];
  ecs_container_name?: string;
  ecs_container_port?: number;
  port?: number;
  protocol?: string;
  registered_targets?: number;
  counts?: Record<string, number>;
}

export interface ClusterTargetGroup extends TargetGroupCheck {
  key: string;
  service: string;
}

export function collectClusterTargetGroups(
  services: ServiceResult[],
): ClusterTargetGroup[] {
  const items: ClusterTargetGroup[] = [];
  for (const item of services) {
    const health = item.checks?.target_group_health as
      | (ReportCheck & { target_groups?: TargetGroupCheck[] })
      | undefined;
    for (const group of health?.target_groups ?? []) {
      items.push({
        ...group,
        key:
          group.target_group_arn ??
          `${item.cluster}::${item.service}::${group.name ?? "tg"}`,
        service: item.service,
      });
    }
  }
  return items;
}

interface Props {
  groups: ClusterTargetGroup[];
}

export function TargetGroupsPanel({ groups }: Props) {
  if (!groups.length) {
    return (
      <div className="empty-panel">
        No target groups are attached to services in this cluster.
      </div>
    );
  }

  const issues = groups.filter((group) => group.attachment_ok === false).length;

  return (
    <div className="target-groups">
      <div className="target-groups-head">
        <h4>Target groups</h4>
        <span className="target-groups-summary">
          {groups.length} group{groups.length === 1 ? "" : "s"}
          {issues ? ` · ${issues} with issues` : ""}
        </span>
      </div>
      <ul className="target-group-list">
        {groups.map((group) => {
          const healthy = group.counts?.healthy ?? 0;
          const unhealthy = group.counts?.unhealthy ?? 0;
          const initial = group.counts?.initial ?? 0;
          const healthBits = [
            healthy ? `${healthy} healthy` : null,
            unhealthy ? `${unhealthy} unhealthy` : null,
            initial ? `${initial} registering` : null,
          ].filter(Boolean);

          return (
            <li
              key={group.key}
              className={`target-group-item ${group.attachment_ok === false ? "issue" : "ok"}`}
            >
              <div className="target-group-title">
                <strong>{group.name ?? "target group"}</strong>
                <StatusBadge
                  status={group.attachment_ok === false ? "FAIL" : "PASS"}
                  label={group.attachment_ok === false ? "Issue" : "OK"}
                />
              </div>
              <div className="target-group-meta">
                <span>Service {group.service}</span>
                {group.protocol && group.port != null && (
                  <span>
                    {group.protocol}:{group.port}
                  </span>
                )}
                {group.ecs_container_name && (
                  <span>
                    {group.ecs_container_name}
                    {group.ecs_container_port != null
                      ? `:${group.ecs_container_port}`
                      : ""}
                  </span>
                )}
                {healthBits.length > 0 && <span>{healthBits.join(" · ")}</span>}
              </div>
              {group.attachment_issues?.length ? (
                <ul className="target-group-issues">
                  {group.attachment_issues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
