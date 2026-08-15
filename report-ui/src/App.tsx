import { useMemo, useState } from "react";
import {
  formatTimestamp,
  groupByCluster,
  loadReport,
  overallStatus,
  sortBySeverity,
  statusLabel,
} from "./utils";
import { StatusBadge } from "./components/StatusBadge";
import { SummaryGrid } from "./components/SummaryGrid";
import { ExecutiveBrief } from "./components/ExecutiveBrief";
import { ServiceCard } from "./components/ServiceCard";
import { ServiceMeshMap } from "./components/ServiceMeshMap";

export default function App() {
  const report = loadReport();
  const clusters = groupByCluster(report.results);
  const overall = overallStatus(report);
  const [openKey, setOpenKey] = useState<string | null>(null);

  const firstIssue = useMemo(() => {
    const issue = sortBySeverity(report.results).find((item) => item.status !== "PASS");
    return issue ? `${issue.cluster}::${issue.service}` : null;
  }, [report.results]);

  const selectService = (key: string) => {
    setOpenKey(key);
    window.requestAnimationFrame(() => {
      document.getElementById(`svc-${key}`)?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  };

  return (
    <div className="page">
      <div className="bg-glow bg-glow-a" />
      <div className="bg-glow bg-glow-b" />

      <main className="shell">
        <header className="hero">
          <div className="hero-top">
            <div>
              <p className="eyebrow">Amazon ECS</p>
              <h1>Service Health Report</h1>
            </div>
            <StatusBadge status={overall} large label={statusLabel(overall)} />
          </div>

          <div className="hero-meta">
            <div className="meta-chip">
              <span className="meta-label">Generated</span>
              <span>{formatTimestamp(report.generated_at)}</span>
            </div>
            <div className="meta-chip">
              <span className="meta-label">Region</span>
              <span className="mono">{report.region}</span>
            </div>
            <div className="meta-chip">
              <span className="meta-label">Account</span>
              <span className="mono">
                {report.account_check.actual_account_id ?? "—"}
              </span>
            </div>
            <div className="meta-chip">
              <span className="meta-label">Version</span>
              <span className="mono">v{report.version}</span>
            </div>
          </div>

          {report.account_check.status === "FAIL" && (
            <div className="account-alert">{report.account_check.message}</div>
          )}
        </header>

        <SummaryGrid summary={report.summary} />
        <ExecutiveBrief report={report} onSelect={selectService} />
        {report.mesh?.nodes?.length ? (
          <ServiceMeshMap mesh={report.mesh} onSelect={selectService} />
        ) : null}

        {report.results.length === 0 ? (
          <section className="empty">No services were checked.</section>
        ) : (
          Array.from(clusters.entries()).map(([cluster, services]) => (
            <section key={cluster} className="cluster-panel">
              <div className="cluster-head">
                <h2>{cluster}</h2>
                <span>{services.length} services · click a row for details</span>
              </div>
              <div className="fleet-legend">
                <span>Service</span>
                <span>Status</span>
                <span>Capacity</span>
                <span>App</span>
                <span>Traffic</span>
                <span>Snapshot</span>
              </div>
              <div className="service-grid">
                {sortBySeverity(services).map((service) => {
                  const key = `${cluster}::${service.service}`;
                  return (
                    <div id={`svc-${key}`} key={key}>
                      <ServiceCard
                        item={service}
                        expanded={openKey === key || (openKey === null && firstIssue === key)}
                        onToggle={() =>
                          setOpenKey((current) => (current === key ? "" : key))
                        }
                      />
                    </div>
                  );
                })}
              </div>
            </section>
          ))
        )}

        <footer className="footer">
          ECS Health Report v{report.version} · One-page snapshot — expand a service
          for engineering detail
        </footer>
      </main>
    </div>
  );
}
