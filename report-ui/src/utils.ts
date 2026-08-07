import type { EcsReport } from "./types";
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
      WARN: "Warning",
      FAIL: "Unhealthy",
    }[status] ?? status
  );
}
