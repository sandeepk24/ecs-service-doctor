import type { ReportCheck, ServiceResult } from "../types";
import { formatTimestamp, statusLabel, toneFromStatus } from "../utils";
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
  onSelect: (item: ServiceResult) => void;
}

export function LogsPanel({ services, selected, onSelect }: Props) {
  const current = selected ?? services[0];
  const logs = current?.checks?.logs as
    | (ReportCheck & { log_groups?: string[]; events?: LogEvent[] })
    | undefined;
  const events = logs?.events ?? [];
  const groups = logs?.log_groups ?? [];

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
