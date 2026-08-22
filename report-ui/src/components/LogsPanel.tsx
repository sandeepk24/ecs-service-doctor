import type { ReportCheck, ServiceResult } from "../types";
import {
  clusterLogSummary,
  humanizeLogMessage,
  leadershipLogHeadline,
  logSummaryFromService,
  logsFromService,
  severityLabel,
  severityTone,
  sortServicesByLogSeverity,
  type LogEvent,
  type LogSeverity,
} from "../logUtils";
import {
  formatTimestamp,
  restartSummary,
  statusLabel,
  toneFromStatus,
} from "../utils";
import { StatusBadge } from "./StatusBadge";

function serviceLogChip(summary: ReturnType<typeof logSummaryFromService>) {
  if (summary.errors) {
    return `${summary.errors} error${summary.errors === 1 ? "" : "s"}`;
  }
  if (summary.warnings) {
    return `${summary.warnings} warn${summary.warnings === 1 ? "" : "s"}`;
  }
  return undefined;
}

function LogLine({ event }: { event: LogEvent }) {
  const severity = (event.severity ?? "info") as LogSeverity;
  const human = humanizeLogMessage(event.message ?? "");
  const raw = (event.message ?? "").trim();
  const showRaw = raw && raw !== human;

  return (
    <li className={`log-line ${severityTone(severity)}`}>
      <div className="log-line-meta">
        <time>{event.timestamp ? formatTimestamp(event.timestamp) : "—"}</time>
        <span className={`log-severity log-severity-${severity}`}>
          {severityLabel(severity)}
        </span>
      </div>
      <div className="log-line-body">
        <p className="log-line-human">{human}</p>
        {showRaw && <p className="log-line-raw">{raw}</p>}
      </div>
    </li>
  );
}

function LogSection({
  title,
  tone,
  events,
}: {
  title: string;
  tone?: string;
  events: LogEvent[];
}) {
  if (!events.length) return null;
  return (
    <section className={`log-section${tone ? ` log-section-${tone}` : ""}`}>
      <h5>{title}</h5>
      <ol className="log-lines">
        {events.map((event, index) => (
          <LogLine key={`${event.timestamp}-${index}`} event={event} />
        ))}
      </ol>
    </section>
  );
}

interface Props {
  services: ServiceResult[];
  selected?: ServiceResult;
  generatedAt: string;
  onSelect: (item: ServiceResult) => void;
}

export function LogsPanel({ services, selected, generatedAt, onSelect }: Props) {
  const ordered = sortServicesByLogSeverity(services);
  const current = selected ?? ordered[0];
  const logs = current?.checks?.logs as
    | (ReportCheck & {
        log_groups?: string[];
        events?: LogEvent[];
        lookback_minutes?: number;
        summary?: {
          errors?: number;
          warnings?: number;
          info?: number;
          total?: number;
        };
      })
    | undefined;
  const events = logsFromService(current ?? { cluster: "", service: "" });
  const attention = events.filter(
    (event) => event.severity === "error" || event.severity === "warning",
  );
  const routine = events.filter((event) => event.severity === "info");
  const groups = logs?.log_groups ?? [];
  const summary = current ? logSummaryFromService(current) : undefined;
  const clusterSummary = clusterLogSummary(services);
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
    <div className="logs-layout">
      {(clusterSummary.errors > 0 || clusterSummary.warnings > 0) && (
        <div className="logs-cluster-summary">
          <strong>
            {clusterSummary.errors > 0
              ? `${clusterSummary.errors} error${clusterSummary.errors === 1 ? "" : "s"}`
              : null}
            {clusterSummary.errors > 0 && clusterSummary.warnings > 0
              ? " · "
              : null}
            {clusterSummary.warnings > 0
              ? `${clusterSummary.warnings} warning${clusterSummary.warnings === 1 ? "" : "s"}`
              : null}
            {" across this cluster"}
          </strong>
          <p>
            Services with issues are listed first. Open each one for plain-language
            explanations and the raw CloudWatch line.
          </p>
        </div>
      )}

      <div className="logs-panel">
        <div className="logs-services">
          {ordered.map((item) => {
            const active =
              item.cluster === current.cluster && item.service === current.service;
            const itemSummary = logSummaryFromService(item);
            const chip = serviceLogChip(itemSummary);
            return (
              <button
                key={`${item.cluster}::${item.service}`}
                type="button"
                className={`logs-service${active ? " active" : ""} ${toneFromStatus(item.status)}`}
                onClick={() => onSelect(item)}
              >
                <span className="logs-service-copy">
                  <strong>{item.service}</strong>
                  {chip && (
                    <span
                      className={`logs-service-chip ${
                        itemSummary.errors ? "critical" : "warning"
                      }`}
                    >
                      {chip}
                    </span>
                  )}
                </span>
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
              </p>
            </div>
          </div>

          {summary && (
            <div className="logs-summary-strip">
              {summary.errors > 0 && (
                <span className="logs-kpi critical">
                  {summary.errors} error{summary.errors === 1 ? "" : "s"}
                </span>
              )}
              {summary.warnings > 0 && (
                <span className="logs-kpi warning">
                  {summary.warnings} warning{summary.warnings === 1 ? "" : "s"}
                </span>
              )}
              {summary.info > 0 && (
                <span className="logs-kpi healthy">
                  {summary.info} routine
                </span>
              )}
              <span className="logs-kpi muted">
                {leadershipLogHeadline(summary, logs?.lookback_minutes)}
              </span>
            </div>
          )}

          {restart && (
            <div className="restart-banner">
              <strong>
                Why it restarted · {restart.count} time
                {restart.count === 1 ? "" : "s"} in the last 12 hours
              </strong>
              {restart.stops.length > 0 ? (
                <ul className="restart-event-list">
                  {restart.stops.map((stop, index) => (
                    <li key={`${stop.stopped_at}-${index}`}>
                      <time>
                        {stop.stopped_at
                          ? formatTimestamp(stop.stopped_at)
                          : "—"}
                      </time>
                      <span>
                        {restart.reasons[index] ??
                          stop.stopped_reason ??
                          "Task stopped"}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <>
                  <p>
                    {restart.reasons.length
                      ? restart.reasons.join(" · ")
                      : "ECS started replacement tasks; no stop reason was recorded."}
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
                </>
              )}
            </div>
          )}

          {events.length === 0 ? (
            <div className="empty-panel">
              {logs?.message || "No recent log lines for this service."}
            </div>
          ) : (
            <>
              <LogSection
                title="Needs attention"
                tone="attention"
                events={attention}
              />
              <LogSection title="Recent activity" tone="routine" events={routine} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
