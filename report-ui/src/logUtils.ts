import type { ServiceResult } from "./types";

export type LogSeverity = "error" | "warning" | "info";

export interface LogEvent {
  timestamp?: string;
  stream?: string;
  container?: string;
  message?: string;
  severity?: LogSeverity;
}

export interface LogSummary {
  errors: number;
  warnings: number;
  info: number;
  total: number;
}

const ERROR_RE =
  /\b(error|exception|fatal|fail(?:ed|ure)?|panic|critical|denied|refused)\b/i;
const WARN_RE = /\b(warn(?:ing)?|deprecated|timeout|503|502|504|429)\b/i;

const SEVERITY_RANK: Record<LogSeverity, number> = {
  error: 0,
  warning: 1,
  info: 2,
};

export function classifyLogSeverity(message: string): LogSeverity {
  if (ERROR_RE.test(message)) return "error";
  if (WARN_RE.test(message)) return "warning";
  return "info";
}

export function normalizeLogEvent(event: LogEvent): LogEvent {
  const message = event.message ?? "";
  return {
    ...event,
    severity: event.severity ?? classifyLogSeverity(message),
  };
}

export function sortLogEvents(events: LogEvent[]): LogEvent[] {
  return [...events].map(normalizeLogEvent).sort((a, b) => {
    const severity =
      SEVERITY_RANK[a.severity ?? "info"] - SEVERITY_RANK[b.severity ?? "info"];
    if (severity !== 0) return severity;
    const aTime = a.timestamp ? Date.parse(a.timestamp) : 0;
    const bTime = b.timestamp ? Date.parse(b.timestamp) : 0;
    return bTime - aTime;
  });
}

export function summarizeLogEvents(events: LogEvent[]): LogSummary {
  const normalized = events.map(normalizeLogEvent);
  const errors = normalized.filter((event) => event.severity === "error").length;
  const warnings = normalized.filter((event) => event.severity === "warning").length;
  const info = normalized.filter((event) => event.severity === "info").length;
  return { errors, warnings, info, total: normalized.length };
}

export function logsFromService(item: ServiceResult): LogEvent[] {
  const logs = item.checks?.logs as { events?: LogEvent[] } | undefined;
  return sortLogEvents(logs?.events ?? []);
}

export function logSummaryFromService(item: ServiceResult): LogSummary {
  const logs = item.checks?.logs as { summary?: LogSummary; events?: LogEvent[] } | undefined;
  if (logs?.summary && typeof logs.summary.total === "number") {
    return logs.summary;
  }
  return summarizeLogEvents(logs?.events ?? []);
}

export function leadershipLogHeadline(
  summary: LogSummary,
  lookbackMinutes?: number,
): string {
  const window = lookbackMinutes ? ` in the last ${lookbackMinutes} min` : "";
  if (summary.errors && summary.warnings) {
    return `${summary.errors} error(s) and ${summary.warnings} warning(s)${window}`;
  }
  if (summary.errors) {
    return `${summary.errors} error(s)${window}`;
  }
  if (summary.warnings) {
    return `${summary.warnings} warning(s)${window}`;
  }
  if (summary.total) {
    return `${summary.total} routine log line(s)${window}`;
  }
  return "No recent log lines";
}

export function humanizeLogMessage(message: string): string {
  let text = message.replace(/\s+/g, " ").trim();
  if (!text) return "No message recorded";

  text = text.replace(/^(ERROR|WARN(?:ING)?|INFO|DEBUG|FATAL)\s+/i, "");

  const patterns: Array<[RegExp, string]> = [
    [/ResourceInitializationError.*SSM/i, "Container failed to start — could not load secrets"],
    [/failed to pull secrets/i, "Container failed to start — secret or registry access problem"],
    [/health check.*503/i, "Health check failing — app returned HTTP 503"],
    [/health check.*502/i, "Health check failing — app returned HTTP 502"],
    [/GET \/health 200/i, "Health check passed"],
    [/listening on/i, "Application started and is listening for traffic"],
    [/registering with target group/i, "Registering with the load balancer"],
    [/connection (?:refused|reset|timed out)/i, "Could not connect to a dependency"],
    [/OutOfMemory/i, "Application ran out of memory"],
    [/Task stopped/i, "ECS stopped the task"],
  ];

  for (const [pattern, label] of patterns) {
    if (pattern.test(text)) return label;
  }

  if (text.length > 140) return `${text.slice(0, 137)}…`;
  return text;
}

export function severityLabel(severity: LogSeverity): string {
  return (
    {
      error: "Error",
      warning: "Warning",
      info: "Routine",
    }[severity] ?? "Routine"
  );
}

export function severityTone(severity: LogSeverity): string {
  return severity === "error" ? "critical" : severity === "warning" ? "warning" : "";
}

export function sortServicesByLogSeverity(services: ServiceResult[]): ServiceResult[] {
  return [...services].sort((a, b) => {
    const aSummary = logSummaryFromService(a);
    const bSummary = logSummaryFromService(b);
    const aRank = aSummary.errors
      ? 0
      : aSummary.warnings
        ? 1
        : aSummary.total
          ? 2
          : 3;
    const bRank = bSummary.errors
      ? 0
      : bSummary.warnings
        ? 1
        : bSummary.total
          ? 2
          : 3;
    if (aRank !== bRank) return aRank - bRank;
    if (bSummary.errors !== aSummary.errors) return bSummary.errors - aSummary.errors;
    if (bSummary.warnings !== aSummary.warnings) {
      return bSummary.warnings - aSummary.warnings;
    }
    return a.service.localeCompare(b.service);
  });
}

export function clusterLogSummary(services: ServiceResult[]): LogSummary {
  return services.reduce(
    (acc, item) => {
      const summary = logSummaryFromService(item);
      acc.errors += summary.errors;
      acc.warnings += summary.warnings;
      acc.info += summary.info;
      acc.total += summary.total;
      return acc;
    },
    { errors: 0, warnings: 0, info: 0, total: 0 },
  );
}

export function servicesWithLogIssues(services: ServiceResult[]): ServiceResult[] {
  return sortServicesByLogSeverity(services).filter((item) => {
    const summary = logSummaryFromService(item);
    return summary.errors > 0 || summary.warnings > 0;
  });
}
