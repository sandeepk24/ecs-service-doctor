import type { ReportCheck, ServiceResult } from "../types";
import {
  formatTimestamp,
  restartedWithinHours,
  serviceMetrics,
  statusLabel,
  toneFromStatus,
} from "../utils";
import { StatusBadge } from "./StatusBadge";

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className={`ops-metric ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function ServiceOpsCard({
  item,
  generatedAt,
  active,
  onSelect,
}: {
  item: ServiceResult;
  generatedAt: string;
  active: boolean;
  onSelect: () => void;
}) {
  const metrics = serviceMetrics(item);
  const tone = toneFromStatus(item.status);
  const restartedAt = restartedWithinHours(item, generatedAt, 12);
  const tasks =
    metrics.running != null && metrics.desired != null
      ? `${metrics.running}/${metrics.desired}`
      : "—";
  const targets =
    metrics.targetTotal != null
      ? `${metrics.targetHealthy ?? 0}/${metrics.targetTotal}`
      : "—";

  return (
    <button
      type="button"
      className={`ops-service-card ${tone}${active ? " active" : ""}`}
      onClick={onSelect}
    >
      <header>
        <span className={`status-pulse ${tone}`} />
        <strong>{item.service}</strong>
        <StatusBadge status={item.status} label={statusLabel(item.status)} />
      </header>
      <p>
        {[metrics.env, item.launch_type].filter(Boolean).join(" · ") || item.cluster}
      </p>
      {restartedAt && (
        <span className="restart-chip" title={`Restarted ${formatTimestamp(restartedAt)}`}>
          Restarted · 12h
        </span>
      )}
      <div className="ops-metric-grid">
        <Metric label="Tasks" value={tasks} />
        <Metric
          label="Targets"
          value={targets}
          tone={
            metrics.targetTotal != null &&
            metrics.targetHealthy === metrics.targetTotal
              ? "healthy"
              : undefined
          }
        />
      </div>
    </button>
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
    <details className="stable-tasks">
      <summary className="stable-tasks-head">
        <h4>Known-good versions</h4>
        <span className="stable-tasks-summary">{tasks.length} revisions</span>
      </summary>
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
    </details>
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

export function ServiceDetail({ item }: { item: ServiceResult }) {
  const checks = item.checks ?? {};
  const events = checks.recent_events?.events ?? [];
  const resources = checks.resources;
  const cpu = resources?.cpu as ReportCheck | undefined;
  const memory = resources?.memory as ReportCheck | undefined;
  const metrics = serviceMetrics(item);

  if (item.error) {
    return <div className="service-error">{item.error}</div>;
  }

  return (
    <div className="service-details panel">
      <div className="ops-detail-facts">
        <div>
          <span className="mini-label">Task definition</span>
          <code>{metrics.revision}</code>
        </div>
        {metrics.lastDeploy && (
          <div>
            <span className="mini-label">Last deployment</span>
            <strong>{formatTimestamp(metrics.lastDeploy)}</strong>
          </div>
        )}
        {metrics.launchDetail && (
          <div>
            <span className="mini-label">Launch</span>
            <strong>{metrics.launchDetail}</strong>
          </div>
        )}
        {metrics.images.length > 0 && (
          <div className="span-2">
            <span className="mini-label">
              Image{metrics.images.length > 1 ? "s" : ""}
            </span>
            {metrics.images.map((entry) => (
              <code key={`${entry.container}-${entry.image}`}>
                {entry.container ? `${entry.container}: ` : ""}
                {entry.image}
              </code>
            ))}
          </div>
        )}
      </div>

      <div className="checks">
        <CheckRow
          label="Capacity"
          status={checks.task_counts?.status}
          message={checks.task_counts?.message}
        />
        <CheckRow label="CPU" status={cpu?.status} message={cpu?.message} />
        <CheckRow
          label="Memory"
          status={memory?.status}
          message={memory?.message}
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
        <CheckRow
          label="Endpoints"
          status={checks.host_header_health?.status}
          message={checks.host_header_health?.message}
        />
      </div>

      <StableTasksSection stableTasks={checks.stable_tasks} />

      {events.length > 0 && (
        <div className="events">
          <h4>Recent events</h4>
          <ul>
            {events.slice(0, 2).map((event, index) => (
              <li key={index}>{event.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

