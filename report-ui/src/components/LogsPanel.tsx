import type { ReportCheck, ServiceResult } from "../types";
import {
  formatTimestamp,
  restartSummary,
  statusLabel,
  toneFromStatus,
} from "../utils";
import { StatusBadge } from "./StatusBadge";

interface LogEvent {
  timestamp?: string;
  stream?: string;
  container?: string;
  message?: string;
}

function logTone(message: string) {
  if (/\b(error|exception|fatal|fail(?:ed|ure)?)\b/i.test(message)) {
    return "critical";
  }
  if (/\b(warn|warning)\b/i.test(message)) return "warning";
  return "";
}

interface Props {
  services: ServiceResult[];
  selected?: ServiceResult;
  generatedAt: string;
  onSelect: (item: ServiceResult) => void;
}

export function LogsPanel({ services, selected, generatedAt, onSelect }: Props) {
  const current = selected ?? services[0];
  const logs = current?.checks?.logs as
    | (ReportCheck & { log_groups?: string[]; events?: LogEvent[] })
    | undefined;
  const events = logs?.events ?? [];
  const groups = logs?.log_groups ?? [];
  const restart = current
    ? restartSummary(current, generatedAt, 12)
    : undefined;
  const ecsEvents =
    (current?.checks?.recent_events?.events as
      | Array<{ created_at?: string; message?: string }>
      | undefined) ?? [];

  if (!current) {
    return <div className="empty-panel">No services in this cluster.</div>;
  }

  return (
    <div className="logs-panel">
      <div className="logs-services">
        {services.map((item) => {
          const active =
            item.cluster === current.cluster && item.service === current.service;
          return (
          <button
            key={`${item.cluster}::${item.service}`}
            type="button"
            className={`logs-service${active ? " active" : ""} ${toneFromStatus(item.status)}`}
            onClick={() => onSelect(item)}
          >
            <strong>{item.service}</strong>
            <StatusBadge status={item.status} label={statusLabel(item.status)} />
          </button>
          );
        })}
      </div>
      <div className="logs-viewer">
        <div className="logs-viewer-head">
          <div>
            <h4>{current.service}</h4>
            <p>
              {groups.length
                ? groups.join(" · ")
                : logs?.message || "No CloudWatch log group"}
              {logs?.lookback_minutes
                ? ` · last ${logs.lookback_minutes} min`
                : ""}
            </p>
          </div>
        </div>
        {restart && (
          <div className="restart-banner">
            <strong>
              Restarted {restart.count} time{restart.count === 1 ? "" : "s"} in
              the last 12 hours
            </strong>
            <p>
              {restart.reasons.length
                ? restart.reasons.join(" · ")
                : "ECS started replacement tasks; no failure reason in recent service events."}
            </p>
            {ecsEvents.length > 0 && (
              <ul className="restart-event-list">
                {ecsEvents.slice(0, 5).map((event, index) => (
                  <li key={`${event.created_at}-${index}`}>
                    <time>
                      {event.created_at
                        ? formatTimestamp(event.created_at)
                        : "—"}
                    </time>
                    <span>{event.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {events.length === 0 ? (
          <div className="empty-panel">
            {logs?.message || "No recent log lines for this service."}
          </div>
        ) : (
          <ol className="log-lines">
            {events.map((event, index) => (
              <li
                key={`${event.timestamp}-${index}`}
                className={`log-line ${logTone(event.message ?? "")}`}
              >
                <time>
                  {event.timestamp ? formatTimestamp(event.timestamp) : "—"}
                </time>
                <code>{event.message}</code>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
