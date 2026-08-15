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
      timeZone: "UTC",
      timeZoneName: "short",
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

export type Tone = "healthy" | "warning" | "critical" | "unknown";

export function toneFromStatus(status?: string): Tone {
  if (status === "PASS") return "healthy";
  if (status === "WARN") return "warning";
  if (status === "FAIL") return "critical";
  return "unknown";
}

export function inferEnvironment(cluster?: string): string | undefined {
  const name = (cluster || "").toLowerCase();
  if (!name) return undefined;
  if (name.includes("prod")) return "PROD";
  if (name.includes("stag")) return "STAGE";
  if (name.includes("dev")) return "DEV";
  if (name.includes("test")) return "TEST";
  return undefined;
}

export function reportEnvironment(report: EcsReport): string | undefined {
  const values = [
    ...new Set(
      report.results
        .map((item) => inferEnvironment(item.cluster))
        .filter((value): value is string => Boolean(value)),
    ),
  ];
  return values.length === 1 ? values[0] : undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function targetCounts(item: ServiceResult): {
  healthy?: number;
  total?: number;
} {
  const groups =
    (item.checks?.target_group_health?.target_groups as
      | Array<{
          registered_targets?: number;
          counts?: Record<string, number>;
        }>
      | undefined) ?? [];
  if (!groups.length) return {};
  let healthy = 0;
  let total = 0;
  for (const group of groups) {
    const counts = group.counts ?? {};
    healthy += counts.healthy ?? 0;
    total +=
      group.registered_targets ??
      (counts.healthy ?? 0) +
        (counts.unhealthy ?? 0) +
        (counts.initial ?? 0);
  }
  return { healthy, total };
}

function usableTimestamp(value?: string): string | undefined {
  if (!value || value === "None" || value === "null") return undefined;
  const time = Date.parse(value);
  return Number.isNaN(time) ? undefined : value;
}

export function lastDeploymentAt(item: ServiceResult): string | undefined {
  const deployments =
    (item.checks?.deployments?.deployments as
      | Array<{
          status?: string;
          created_at?: string;
          updated_at?: string;
        }>
      | undefined) ?? [];
  const primary =
    deployments.find((deployment) => deployment.status === "PRIMARY") ??
    deployments[0];
  const fromDeploy =
    usableTimestamp(primary?.created_at) ?? usableTimestamp(primary?.updated_at);
  if (fromDeploy) return fromDeploy;

  const events =
    (item.checks?.recent_events?.events as
      | Array<{ created_at?: string; message?: string }>
      | undefined) ?? [];
  const started = events.find((event) =>
    /has started \d+ tasks|deployment|force.new.deployment/i.test(
      event.message ?? "",
    ),
  );
  if (usableTimestamp(started?.created_at)) return started?.created_at;

  const stable = item.checks?.stable_tasks as
    | { stable_tasks?: Array<{ is_current?: boolean; last_stable_at?: string }> }
    | undefined;
  const current = stable?.stable_tasks?.find((task) => task.is_current);
  return usableTimestamp(current?.last_stable_at);
}

export function serviceMetrics(item: ServiceResult) {
  const counts = item.checks?.task_counts;
  const targets = targetCounts(item);
  const cpu = item.checks?.resources?.cpu as
    | {
        utilization?: number;
        message?: string;
        status?: string;
        reserved_label?: string;
      }
    | undefined;
  const memory = item.checks?.resources?.memory as
    | {
        utilization?: number;
        message?: string;
        status?: string;
        reserved_label?: string;
      }
    | undefined;
  const taskDef = item.checks?.task_definition as
    | {
        cpu?: string;
        memory?: string;
        network_mode?: string;
        requires_compatibilities?: string[];
        container_images?: Array<{ container: string; image: string }>;
      }
    | undefined;
  const images = taskDef?.container_images ?? [];
  const launchBits = [
    item.launch_type,
    item.platform_version ? `platform ${item.platform_version}` : undefined,
    cpu?.reserved_label && memory?.reserved_label
      ? `${cpu.reserved_label} / ${memory.reserved_label}`
      : cpu?.reserved_label || memory?.reserved_label,
    taskDef?.network_mode,
  ].filter(Boolean);

  return {
    running: asNumber(counts?.running),
    desired: asNumber(counts?.desired),
    pending: asNumber(counts?.pending),
    deployStatus: item.checks?.deployments?.status,
    deployMessage: item.checks?.deployments?.message,
    targetHealthy: targets.healthy,
    targetTotal: targets.total,
    cpuUtil: asNumber(cpu?.utilization),
    memUtil: asNumber(memory?.utilization),
    cpuStatus: cpu?.status,
    memStatus: memory?.status,
    cpuReserved: cpu?.reserved_label,
    memReserved: memory?.reserved_label,
    images,
    image: images.map((entry) => entry.image).join("\n") || undefined,
    revision: shortTaskDefinition(item.task_definition),
    lastDeploy: lastDeploymentAt(item),
    env: inferEnvironment(item.cluster),
    launchDetail: launchBits.join(" · ") || undefined,
    networkMode: taskDef?.network_mode,
    platformVersion: item.platform_version,
  };
}

export function fleetMetrics(report: EcsReport) {
  let running = 0;
  let desired = 0;
  let tasksKnown = false;
  let healthyTargets = 0;
  let totalTargets = 0;
  let targetsKnown = false;
  let failedDeploys = 0;
  let rollingDeploys = 0;

  for (const item of report.results) {
    const metrics = serviceMetrics(item);
    if (metrics.running != null && metrics.desired != null) {
      running += metrics.running;
      desired += metrics.desired;
      tasksKnown = true;
    }
    if (metrics.targetTotal != null) {
      healthyTargets += metrics.targetHealthy ?? 0;
      totalTargets += metrics.targetTotal;
      targetsKnown = true;
    }
    if (metrics.deployStatus === "FAIL") failedDeploys += 1;
    if (metrics.deployStatus === "WARN") rollingDeploys += 1;
  }

  const total = report.summary.total_services || report.results.length;
  const passed = report.summary.passed;
  const warnings = report.summary.warnings;
  const failed = report.summary.failed;
  const healthPct = total ? Math.round((passed / total) * 100) : 0;

  return {
    running,
    desired,
    tasksKnown,
    healthyTargets,
    totalTargets,
    targetsKnown,
    failedDeploys,
    rollingDeploys,
    passed,
    total,
    warnings,
    failed,
    healthPct,
    failPct: total ? Math.round((failed / total) * 100) : 0,
    warnPct: total ? Math.round((warnings / total) * 100) : 0,
    passPct: total ? Math.round((passed / total) * 100) : 0,
  };
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
