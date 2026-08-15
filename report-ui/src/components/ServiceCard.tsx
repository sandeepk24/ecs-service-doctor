import type { ReportCheck, ServiceResult } from "../types";
import type { Topology } from "./TopologyDiagram";
import {
  httpLabel,
  serviceLight,
  serviceSnapshot,
  shortTaskDefinition,
  statusLabel,
  taskCapacity,
  trafficLabel,
} from "../utils";
import { StatusBadge } from "./StatusBadge";
import { StatusLight } from "./StatusLight";
import { LoadBalancerPanel } from "./LoadBalancerPanel";

interface Props {
  item: ServiceResult;
  selected: boolean;
  onSelect: () => void;
}

interface TargetGroupCheck {
  name?: string;
  attachment_ok?: boolean;
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
  };
}) {
  const groups = targetHealth?.target_groups ?? [];
  if (!groups.length) return null;

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
          const healthBits = [
            healthy ? `${healthy} healthy` : null,
            unhealthy ? `${unhealthy} unhealthy` : null,
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
                  label={group.attachment_ok ? "OK" : "Issue"}
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
                    {group.ecs_container_name}
                    {group.ecs_container_port != null ? `:${group.ecs_container_port}` : ""}
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

function StableTasksSection({
  stableTasks,
}: {
  stableTasks?: ReportCheck & {
    stable_tasks?: Array<{
      task_definition?: string;
      image?: string;
      last_stable_at?: string;
      is_current?: boolean;
      rollback_command?: string;
    }>;
  };
}) {
  const tasks = stableTasks?.stable_tasks ?? [];
  if (!tasks.length) return null;

  return (
    <div className="stable-tasks">
      <div className="stable-tasks-head">
        <h4>Known-good versions</h4>
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
        {status && <StatusBadge status={status} label={statusLabel(status)} />}
        <span>{message}</span>
      </div>
    </div>
  );
}

export function ServiceTile({ item, selected, onSelect }: Props) {
  return (
    <button
      type="button"
      className={`service-tile ${item.status.toLowerCase()}${selected ? " selected" : ""}`}
      onClick={onSelect}
    >
      <div className="service-tile-top">
        <StatusLight light={serviceLight(item)} />
        <StatusBadge status={item.status} label={statusLabel(item.status)} />
      </div>
      <h3>{item.service}</h3>
      {item.critical && <span className="critical-tag">Critical</span>}
      <p className="service-snapshot">{serviceSnapshot(item)}</p>
      <div className="service-tile-metrics">
        <span>
          <span className="mini-label">Capacity</span>
          <strong>{taskCapacity(item)}</strong>
        </span>
        <span>
          <span className="mini-label">App</span>
          <strong>{httpLabel(item)}</strong>
        </span>
        <span>
          <span className="mini-label">Traffic</span>
          <strong>{trafficLabel(item)}</strong>
        </span>
      </div>
    </button>
  );
}

export function ServiceDetail({ item }: { item: ServiceResult }) {
  const checks = item.checks ?? {};
  const events = checks.recent_events?.events ?? [];
  const images = checks.task_definition?.container_images ?? [];
  const connectivity = checks.connectivity as Topology | undefined;

  if (item.error) {
    return <div className="service-error">{item.error}</div>;
  }

  return (
    <div className="service-details panel">
      <div className="meta-grid">
        <div>
          <span className="mini-label">Task definition</span>
          <code>{shortTaskDefinition(item.task_definition)}</code>
        </div>
        {item.launch_type && (
          <div>
            <span className="mini-label">Launch</span>
            <strong>{item.launch_type}</strong>
          </div>
        )}
      </div>

      <div className="checks">
        <CheckRow
          label="Capacity"
          status={checks.task_counts?.status}
          message={checks.task_counts?.message}
        />
        <CheckRow
          label="Release"
          status={checks.deployments?.status}
          message={checks.deployments?.message}
        />
        <CheckRow
          label="Traffic"
          status={checks.target_group_health?.status}
          message={checks.target_group_health?.message}
        />
        <CheckRow
          label="App"
          status={checks.http_health?.status}
          message={checks.http_health?.message}
        />
      </div>

      <TargetGroupSection targetHealth={checks.target_group_health} />
      <LoadBalancerPanel loadBalancers={connectivity?.load_balancers} />
      <StableTasksSection stableTasks={checks.stable_tasks} />

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
            {events.slice(0, 4).map((event, index) => (
              <li key={index}>{event.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
