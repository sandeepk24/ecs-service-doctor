import { useEffect, useMemo, useState } from "react";
import {
  formatTimestamp,
  groupByCluster,
  loadReport,
  overallStatus,
  reportEnvironment,
  sortBySeverity,
  statusLabel,
  serviceMetrics,
  toneFromStatus,
} from "./utils";
import type { ServiceResult } from "./types";
import { StatusBadge } from "./components/StatusBadge";
import { ExecutiveBrief } from "./components/ExecutiveBrief";
import { HealthBar, KpiStrip } from "./components/KpiStrip";
import { ServiceDetail, ServiceOpsCard } from "./components/ServiceCard";
import {
  collectClusterLoadBalancers,
  LoadBalancerPanel,
} from "./components/LoadBalancerPanel";
import {
  collectClusterTargetGroups,
  TargetGroupsPanel,
} from "./components/TargetGroupsPanel";
import {
  collectClusterDnsRecords,
  Route53Panel,
} from "./components/Route53Panel";
import {
  collectClusterBackends,
  BackendsPanel,
} from "./components/BackendsPanel";
import { LogsPanel } from "./components/LogsPanel";
import { EcsLogo } from "./components/EcsLogo";

type ClusterSection =
  | "services"
  | "target-groups"
  | "load-balancers"
  | "route-53"
  | "backends"
  | "logs";

type ThemeMode = "night" | "day";

const THEME_KEY = "ecs-doctor-theme";

function serviceKey(item: ServiceResult) {
  return `${item.cluster}::${item.service}`;
}

function readStoredTheme(): ThemeMode {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    if (value === "day" || value === "night") return value;
  } catch {
    /* ignore */
  }
  return "night";
}

export default function App() {
  const report = loadReport();
  const clusters = groupByCluster(report.results);
  const overall = overallStatus(report);
  const overallTone = toneFromStatus(overall);
  const environment = reportEnvironment(report);
  const defaultKey = useMemo(() => {
    const issue = sortBySeverity(report.results).find(
      (item) => item.status !== "PASS",
    );
    return issue
      ? serviceKey(issue)
      : report.results[0]
        ? serviceKey(report.results[0])
        : "";
  }, [report.results]);

  const [selectedKey, setSelectedKey] = useState(defaultKey);
  const [sectionByCluster, setSectionByCluster] = useState<
    Record<string, ClusterSection>
  >({});
  const [theme, setTheme] = useState<ThemeMode>(() => readStoredTheme());

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const selectService = (key: string) => {
    const cluster = key.split("::")[0];
    setSectionByCluster((prev) => ({ ...prev, [cluster]: "services" }));
    setSelectedKey(key);
    window.requestAnimationFrame(() => {
      document.getElementById(`cluster-${cluster}`)?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  };

  const operational = `${report.summary.passed}/${report.summary.total_services} services operational`;

  return (
    <div className="page">
      <div className="bg-glow bg-glow-a" />
      <div className="bg-glow bg-glow-b" />
      <div className="bg-glow bg-glow-c" />
      <main className="shell">
        <header className="ops-header hero">
          <div className="ops-brand">
            <EcsLogo className="ops-ecs-logo" />
            <div>
              <p className="eyebrow">Amazon ECS</p>
              <h1>Service Health</h1>
              <p className="ops-subhead">
                {operational}
                <span> · Last checked {formatTimestamp(report.generated_at)}</span>
              </p>
            </div>
          </div>
          <div className="ops-header-side">
            <div
              className="theme-toggle"
              role="group"
              aria-label="Color theme"
            >
              <button
                type="button"
                className={theme === "day" ? "active" : ""}
                aria-pressed={theme === "day"}
                onClick={() => setTheme("day")}
              >
                Day
              </button>
              <button
                type="button"
                className={theme === "night" ? "active" : ""}
                aria-pressed={theme === "night"}
                onClick={() => setTheme("night")}
              >
                Night
              </button>
            </div>
            <span className="ops-env gloss-pill">
              {environment ? `${environment} · ` : ""}
              {report.region}
            </span>
            <span className={`ops-health ${overallTone}`}>
              <span className={`status-pulse ${overallTone}`} />
              {overall === "PASS"
                ? "ALL SYSTEMS HEALTHY"
                : overall === "WARN"
                  ? "SYSTEMS DEGRADED"
                  : "SYSTEMS UNHEALTHY"}
            </span>
          </div>
        </header>

        {report.account_check.status === "FAIL" && (
          <div className="account-alert">{report.account_check.message}</div>
        )}

        <KpiStrip report={report} />
        <HealthBar report={report} />
        <ExecutiveBrief report={report} onSelect={selectService} />

        {report.results.length === 0 ? (
          <section className="empty">No services were checked.</section>
        ) : (
          Array.from(clusters.entries()).map(([cluster, services]) => {
            const ordered = sortBySeverity(services);
            const selected =
              ordered.find((item) => serviceKey(item) === selectedKey) ??
              ordered[0];
            const section = sectionByCluster[cluster] ?? "services";
            const targetGroups = collectClusterTargetGroups(services);
            const loadBalancers = collectClusterLoadBalancers(services);
            const dnsRecords = collectClusterDnsRecords(services);
            const backends = collectClusterBackends(ordered);

            return (
              <section
                key={cluster}
                id={`cluster-${cluster}`}
                className="cluster-panel"
              >
                <div className="cluster-head">
                  <h2>{cluster}</h2>
                  <span>{services.length} services</span>
                </div>

                <div
                  className="section-tabs"
                  role="tablist"
                  aria-label={`${cluster} views`}
                >
                  {(
                    [
                      ["services", "Services", services.length],
                      ["backends", "Backends", backends.length],
                      ["target-groups", "Target groups", targetGroups.length],
                      ["load-balancers", "Load balancers", loadBalancers.length],
                      ["route-53", "Route 53", dnsRecords.length],
                      ["logs", "Logs", services.length],
                    ] as const
                  ).map(([id, label, count]) => (
                    <button
                      key={id}
                      type="button"
                      role="tab"
                      aria-selected={section === id}
                      className={`section-tab${section === id ? " active" : ""}`}
                      onClick={() =>
                        setSectionByCluster((prev) => ({
                          ...prev,
                          [cluster]: id,
                        }))
                      }
                    >
                      {label}
                      <em>{count}</em>
                    </button>
                  ))}
                </div>

                {section === "services" && (
                  <>
                    <div className="ops-service-grid">
                      {ordered.map((item) => {
                        const key = serviceKey(item);
                        return (
                          <ServiceOpsCard
                            key={key}
                            item={item}
                            generatedAt={report.generated_at}
                            active={selected && serviceKey(selected) === key}
                            onSelect={() => setSelectedKey(key)}
                          />
                        );
                      })}
                    </div>

                    {selected && (
                      <div className="service-tab-panel" role="tabpanel">
                        <div className="service-tab-panel-head">
                          <div>
                            <h3>{selected.service}</h3>
                            <p className="ops-detail-meta">
                              {selected.cluster}
                              {serviceMetrics(selected).env
                                ? ` · ${serviceMetrics(selected).env}`
                                : ""}
                              {selected.launch_type
                                ? ` · ${selected.launch_type}`
                                : ""}
                            </p>
                          </div>
                          <StatusBadge
                            status={selected.status}
                            label={statusLabel(selected.status)}
                          />
                        </div>
                        <ServiceDetail item={selected} />
                      </div>
                    )}
                  </>
                )}

                {section === "target-groups" && (
                  <TargetGroupsPanel groups={targetGroups} />
                )}

                {section === "load-balancers" && (
                  <LoadBalancerPanel loadBalancers={loadBalancers} />
                )}

                {section === "route-53" && (
                  <Route53Panel records={dnsRecords} scan={report.route53} />
                )}

                {section === "backends" && (
                  <BackendsPanel
                    backends={backends}
                    serviceOrder={ordered.map((item) => item.service)}
                  />
                )}

                {section === "logs" && (
                  <LogsPanel
                    services={ordered}
                    selected={selected}
                    generatedAt={report.generated_at}
                    onSelect={(item) => setSelectedKey(serviceKey(item))}
                  />
                )}
              </section>
            );
          })
        )}

        <footer className="footer">
          ECS Health Report v{report.version} · Account{" "}
          {report.account_check.actual_account_id ?? "—"} · {report.region}
        </footer>
      </main>
    </div>
  );
}
