import type { ReportCheck, ServiceResult, Topology } from "../types";
import { statusLabel } from "../utils";
import { StatusBadge } from "./StatusBadge";
import { TopologyDiagram } from "./TopologyDiagram";

interface Props {
  item: ServiceResult;
}

interface TargetGroupCheck {
  name?: string;
  attachment_ok?: boolean;
  attachment_summary?: string;
  attachment_issues?: string[];
  ecs_container_name?: string;
  ecs_container_port?: number;
  port?: number;
  protocol?: string;
  registered_targets?: number;
  counts?: Record<string, number>;
}

function TargetGroupSection({
  targetHealth,
}: {
  targetHealth?: ReportCheck & {
    target_groups?: TargetGroupCheck[];
    attachment_summary?: string;
    attachment_ok?: boolean;
  };
}) {
  const groups = targetHealth?.target_groups ?? [];
  if (!groups.length) {
    return null;
  }

  return (
    <div className="target-groups">
      <div className="target-groups-head">
        <h4>Target groups</h4>
        {targetHealth?.attachment_summary && (
          <span className="target-groups-summary">{targetHealth.attachment_summary}</span>
        )}
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
              key={group.name ?? group.ecs_container_name}
              className={`target-group-item ${group.attachment_ok ? "ok" : "issue"}`}
            >
              <div className="target-group-title">
                <strong>{group.name ?? "target group"}</strong>
                <StatusBadge
                  status={group.attachment_ok ? "PASS" : "FAIL"}
                  label={group.attachment_ok ? "Attached OK" : "Attachment issue"}
                />
              </div>
              <div className="target-group-meta">
                {group.protocol && group.port != null && (
                  <span>
                    {group.protocol}:{group.port}
                  </span>
                )}
                {group.ecs_container_name && (
                  <span>
                    → {group.ecs_container_name}
                    {group.ecs_container_port != null ? `:${group.ecs_container_port}` : ""}
                  </span>
                )}
                {group.registered_targets != null && (
                  <span>{group.registered_targets} registered</span>
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

function StableTasksSection({
  stableTasks,
}: {
  stableTasks?: ReportCheck & {
    stable_tasks?: Array<{
      task_definition?: string;
      image?: string;
      last_stable_at?: string;
      is_current?: boolean;
      source?: string;
      rollback_command?: string;
    }>;
    message?: string;
  };
}) {
  const tasks = stableTasks?.stable_tasks ?? [];
  if (!tasks.length) {
    return null;
  }

  return (
    <div className="stable-tasks">
      <div className="stable-tasks-head">
        <h4>Stable tasks (rollback)</h4>
        {stableTasks?.message && (
          <span className="stable-tasks-summary">{stableTasks.message}</span>
        )}
      </div>
      <ul className="stable-task-list">
        {tasks.map((task) => (
          <li key={task.task_definition} className="stable-task-item">
            <div className="stable-task-title">
              <code>{task.task_definition}</code>
              {task.is_current && <span className="current-tag">Current</span>}
            </div>
            {task.image && (
              <div className="stable-task-image">
                <span className="mini-label">Image</span>
                <code>{task.image}</code>
              </div>
            )}
            <div className="stable-task-meta">
              {task.last_stable_at && <span>Last stable {task.last_stable_at}</span>}
              {task.source && <span>{task.source.replace(/_/g, " ")}</span>}
            </div>
            {task.rollback_command && (
              <div className="rollback-command">
                <span className="mini-label">Rollback</span>
                <code>{task.rollback_command}</code>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CheckRow({
  label,
  status,
  message,
}: {
  label: string;
  status?: string;
  message?: string;
}) {
  if (!message) return null;
  return (
    <div className="check-row">
      <div className="check-label">{label}</div>
      <div className="check-value">
        {status && <StatusBadge status={status} />}
        <span>{message}</span>
      </div>
    </div>
  );
}

export function ServiceCard({ item }: Props) {
  const checks = item.checks ?? {};
  const events = checks.recent_events?.events ?? [];
  const images = checks.task_definition?.container_images ?? [];
  const connectivity = checks.connectivity as Topology | undefined;

  return (
    <article className={`service-card ${item.status.toLowerCase()}`}>
      <header className="service-head">
        <div>
          <h3>{item.service}</h3>
          {item.critical && <span className="critical-tag">Critical</span>}
        </div>
        <StatusBadge status={item.status} label={statusLabel(item.status)} />
      </header>

      {item.error ? (
        <div className="service-error">{item.error}</div>
      ) : (
        <>
          <div className="meta-grid">
            {item.task_definition && (
              <div>
                <span className="mini-label">Task definition</span>
                <code>{item.task_definition}</code>
              </div>
            )}
            {item.launch_type && (
              <div>
                <span className="mini-label">Launch</span>
                <strong>{item.launch_type}</strong>
              </div>
            )}
            {item.platform_version && (
              <div>
                <span className="mini-label">Platform</span>
                <strong>{item.platform_version}</strong>
              </div>
            )}
          </div>

          <div className="checks">
            <CheckRow
              label="Tasks"
              status={checks.task_counts?.status}
              message={checks.task_counts?.message}
            />
            <CheckRow
              label="Deployment"
              status={checks.deployments?.status}
              message={checks.deployments?.message}
            />
            <CheckRow
              label="Load balancer"
              status={checks.target_group_health?.status}
              message={checks.target_group_health?.message}
            />
            <CheckRow
              label="HTTP"
              status={checks.http_health?.status}
              message={checks.http_health?.message}
            />
          </div>

          <TargetGroupSection targetHealth={checks.target_group_health} />

          <StableTasksSection stableTasks={checks.stable_tasks} />

          {connectivity?.nodes?.length ? (
            <TopologyDiagram topology={connectivity} />
          ) : null}

          {images.length > 0 && (
            <div className="images">
              {images.map((image) => (
                <div key={image.container} className="image-row">
                  <span className="mini-label">{image.container}</span>
                  <code>{image.image}</code>
                </div>
              ))}
            </div>
          )}

          {events.length > 0 && (
            <div className="events">
              <h4>Recent events</h4>
              <ul>
                {events.slice(0, 8).map((event, index) => (
                  <li key={index}>{event.message}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </article>
  );
}
