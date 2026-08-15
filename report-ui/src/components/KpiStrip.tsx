import type { EcsReport } from "../types";
import { fleetMetrics, overallStatus, toneFromStatus } from "../utils";

export function KpiStrip({ report }: { report: EcsReport }) {
  const metrics = fleetMetrics(report);
  const overall = toneFromStatus(overallStatus(report));

  return (
    <section className="ops-kpis" aria-label="Fleet KPIs">
      <article className={`kpi-card ${metrics.passed === metrics.total ? "healthy" : overall}`}>
        <span className="kpi-label">Services</span>
        <strong>
          {metrics.passed} / {metrics.total}
          {metrics.passed === metrics.total && metrics.total > 0 ? " ✓" : ""}
        </strong>
        <span className="kpi-hint">Healthy</span>
      </article>
      <article
        className={`kpi-card ${
          metrics.tasksKnown && metrics.running === metrics.desired
            ? "healthy"
            : metrics.tasksKnown
              ? "warning"
              : "unknown"
        }`}
      >
        <span className="kpi-label">Tasks</span>
        <strong>
          {metrics.tasksKnown ? `${metrics.running} / ${metrics.desired}` : "—"}
          {metrics.tasksKnown && metrics.running === metrics.desired ? " ✓" : ""}
        </strong>
        <span className="kpi-hint">Running</span>
      </article>
      <article
        className={`kpi-card ${
          metrics.targetsKnown && metrics.healthyTargets === metrics.totalTargets
            ? "healthy"
            : metrics.targetsKnown
              ? "warning"
              : "unknown"
        }`}
      >
        <span className="kpi-label">Targets</span>
        <strong>
          {metrics.targetsKnown
            ? `${metrics.healthyTargets} / ${metrics.totalTargets}`
            : "—"}
          {metrics.targetsKnown &&
          metrics.healthyTargets === metrics.totalTargets
            ? " ✓"
            : ""}
        </strong>
        <span className="kpi-hint">Healthy</span>
      </article>
      <article
        className={`kpi-card ${
          metrics.failedDeploys
            ? "critical"
            : metrics.rollingDeploys
              ? "warning"
              : "healthy"
        }`}
      >
        <span className="kpi-label">Deployments</span>
        <strong>
          {metrics.failedDeploys
            ? String(metrics.failedDeploys)
            : metrics.rollingDeploys
              ? String(metrics.rollingDeploys)
              : "0"}
        </strong>
        <span className="kpi-hint">
          {metrics.failedDeploys
            ? "Failed"
            : metrics.rollingDeploys
              ? "Rolling"
              : "Failed"}
        </span>
      </article>
    </section>
  );
}

export function HealthBar({ report }: { report: EcsReport }) {
  const metrics = fleetMetrics(report);
  return (
    <section className="health-bar" aria-label="Overall health">
      <span className="health-bar-label">Overall Health</span>
      <div
        className="health-bar-track"
        title={`${metrics.passed} healthy · ${metrics.warnings} warning · ${metrics.failed} unhealthy`}
      >
        {metrics.failPct > 0 && (
          <span className="critical" style={{ width: `${metrics.failPct}%` }} />
        )}
        {metrics.warnPct > 0 && (
          <span className="warning" style={{ width: `${metrics.warnPct}%` }} />
        )}
        <span className="healthy" style={{ width: `${metrics.passPct}%` }} />
      </div>
      <strong>{metrics.healthPct}%</strong>
    </section>
  );
}
