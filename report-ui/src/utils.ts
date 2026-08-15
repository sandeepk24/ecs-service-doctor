import type { EcsReport, ServiceResult, Status } from "./types";
import { sampleReport } from "./sampleData";

export function loadReport(): EcsReport {
  const node = document.getElementById("ecs-report-data");
  const raw = node?.textContent?.trim() ?? "";

  if (!raw || raw === "__ECS_REPORT_JSON__") {
    return sampleReport;
  }

  try {
    return JSON.parse(raw) as EcsReport;
  } catch {
    return sampleReport;
  }
}

export function groupByCluster(results: EcsReport["results"]) {
  const map = new Map<string, EcsReport["results"]>();
  for (const item of results) {
    const list = map.get(item.cluster) ?? [];
    list.push(item);
    map.set(item.cluster, list);
  }
  return map;
}

export function overallStatus(report: EcsReport): "PASS" | "WARN" | "FAIL" {
  if (report.account_check.status === "FAIL") return "FAIL";
  if (report.summary.failed > 0) return "FAIL";
  if (report.summary.warnings > 0) return "WARN";
  return "PASS";
}

export function formatTimestamp(value: string) {
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

export function statusLabel(status: string) {
  return (
    {
      PASS: "Healthy",
      WARN: "Needs attention",
      FAIL: "Unhealthy",
    }[status] ?? status
  );
}

const SEVERITY: Record<Status, number> = { FAIL: 0, WARN: 1, PASS: 2 };

export function sortBySeverity(results: ServiceResult[]): ServiceResult[] {
  return [...results].sort((a, b) => {
    const severity = SEVERITY[a.status] - SEVERITY[b.status];
    if (severity !== 0) return severity;
    if (Boolean(b.critical) !== Boolean(a.critical)) {
      return Number(b.critical) - Number(a.critical);
    }
    return a.service.localeCompare(b.service);
  });
}

export function executiveHeadline(report: EcsReport): string {
  const total = report.summary.total_services;
  const failed = report.summary.failed;
  const warnings = report.summary.warnings;
  if (report.account_check.status === "FAIL") {
    return "This report did not run — AWS account check failed";
  }
  if (failed === 0 && warnings === 0) {
    return total === 1
      ? "The application is healthy"
      : `All ${total} services are healthy`;
  }
  if (failed > 0) {
    return failed === 1
      ? "1 service is unhealthy"
      : `${failed} of ${total} services are unhealthy`;
  }
  return warnings === 1
    ? "1 service needs attention"
    : `${warnings} of ${total} services need attention`;
}

export function executiveSubhead(report: EcsReport): string {
  const { passed, warnings, failed, total_services: total } = report.summary;
  const bits = [`${passed} healthy`];
  if (warnings) {
    bits.push(
      warnings === 1 ? "1 needs attention" : `${warnings} need attention`,
    );
  }
  if (failed) bits.push(`${failed} unhealthy`);
  return `${total} services checked · ${bits.join(" · ")}`;
}

export function attentionItems(results: ServiceResult[]): ServiceResult[] {
  return sortBySeverity(results).filter((item) => item.status !== "PASS");
}

export function taskCapacity(item: ServiceResult): string {
  const counts = item.checks?.task_counts;
  const running = counts?.running as number | undefined;
  const desired = counts?.desired as number | undefined;
  if (typeof running === "number" && typeof desired === "number") {
    return `${running} of ${desired}`;
  }
  return "—";
}

export function httpLabel(item: ServiceResult): string {
  const http = item.checks?.http_health;
  if (!http) return "—";
  const code = http.http_status as number | undefined;
  if (http.status === "PASS") return code ? `OK · ${code}` : "OK";
  if (typeof code === "number") return `HTTP ${code}`;
  return "Unavailable";
}

export function trafficLabel(item: ServiceResult): string {
  const lb = item.checks?.target_group_health;
  if (!lb) return "—";
  if (lb.status === "PASS") return "OK";
  if (lb.status === "WARN") return "Watch";
  return "Issue";
}

export function serviceSnapshot(item: ServiceResult): string {
  if (item.error) return item.error;
  if (item.status === "PASS") return "Operating normally";

  const checks = item.checks ?? {};
  const http = checks.http_health;
  if (http?.status === "FAIL") {
    const code = http.http_status as number | undefined;
    return typeof code === "number"
      ? `App health check failed (got HTTP ${code})`
      : "App health endpoint is not reachable";
  }

  const hostRoutes = checks.host_header_health;
  if (hostRoutes?.status === "FAIL") {
    return hostRoutes.message ?? "An endpoint health check failed";
  }

  const tasks = checks.task_counts;
  if (tasks?.status === "FAIL") {
    const running = tasks.running as number | undefined;
    const desired = tasks.desired as number | undefined;
    if (typeof running === "number" && typeof desired === "number") {
      return `Capacity is low — ${running} of ${desired} tasks running`;
    }
    return tasks.message ?? "Task capacity is below expected";
  }

  const lb = checks.target_group_health;
  if (lb?.status === "FAIL") {
    return "Load balancer is seeing unhealthy targets";
  }
  if (lb?.status === "WARN") {
    return "Traffic is still settling after a deploy";
  }

  const deploy = checks.deployments;
  if (deploy?.status === "FAIL") {
    return "A new version is not finishing rollout";
  }
  if (deploy?.status === "WARN") {
    return "A new version is still rolling out";
  }

  return "Needs review";
}

export function shortTaskDefinition(arn?: string): string {
  if (!arn) return "—";
  return arn.split("/").pop() ?? arn;
}

export function serviceLight(item: ServiceResult): "green" | "red" {
  const http = item.checks?.http_health;
  const hosts = item.checks?.host_header_health;
  if (http) {
    const expected = (http.expected_status as number | undefined) ?? 200;
    const code = http.http_status as number | undefined;
    if (http.status === "PASS" && (code === undefined || code === expected)) {
      if (hosts?.status === "FAIL") return "red";
      return "green";
    }
    return "red";
  }
  if (hosts?.status === "FAIL") return "red";
  const running = item.checks?.task_counts?.running as number | undefined;
  if (item.status === "FAIL" || !running) return "red";
  return "green";
}
