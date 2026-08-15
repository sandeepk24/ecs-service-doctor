import { useMemo, useState } from "react";
import {
  executiveHeadline,
  executiveSubhead,
  formatTimestamp,
  groupByCluster,
  loadReport,
  overallStatus,
  sortBySeverity,
  statusLabel,
  serviceLight,
} from "./utils";
import type { ServiceResult } from "./types";
import { StatusBadge } from "./components/StatusBadge";
import { StatusLight } from "./components/StatusLight";
import { ExecutiveBrief } from "./components/ExecutiveBrief";
import { ServiceDetail } from "./components/ServiceCard";
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

type ClusterSection =
  | "services"
  | "target-groups"
  | "load-balancers"
  | "route-53";

function serviceKey(item: ServiceResult) {
  return `${item.cluster}::${item.service}`;
}

export default function App() {
  const report = loadReport();
  const clusters = groupByCluster(report.results);
  const overall = overallStatus(report);
  const defaultKey = useMemo(() => {
    const issue = sortBySeverity(report.results).find((item) => item.status !== "PASS");
    return issue ? serviceKey(issue) : report.results[0] ? serviceKey(report.results[0]) : "";
  }, [report.results]);

  const [selectedKey, setSelectedKey] = useState(defaultKey);
  const [sectionByCluster, setSectionByCluster] = useState<
    Record<string, ClusterSection>
  >({});

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
              <p className="hero-headline">{executiveHeadline(report)}</p>
            </div>
            <StatusBadge status={overall} large label={statusLabel(overall)} />
          </div>

          <div className="hero-meta compact">
            <div className="meta-chip">
              <span className="meta-label">Fleet</span>
              <span>{executiveSubhead(report)}</span>
            </div>
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
          </div>

          {report.account_check.status === "FAIL" && (
            <div className="account-alert">{report.account_check.message}</div>
          )}
        </header>

        <ExecutiveBrief report={report} onSelect={selectService} />

        {report.results.length === 0 ? (
          <section className="empty">No services were checked.</section>
        ) : (
          Array.from(clusters.entries()).map(([cluster, services]) => {
            const ordered = sortBySeverity(services);
            const selected =
              ordered.find((item) => serviceKey(item) === selectedKey) ?? ordered[0];
            const section = sectionByCluster[cluster] ?? "services";
            const targetGroups = collectClusterTargetGroups(services);
            const loadBalancers = collectClusterLoadBalancers(services);
            const dnsRecords = collectClusterDnsRecords(services);

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

                <div className="section-tabs" role="tablist" aria-label={`${cluster} views`}>
                  {(
                    [
                      ["services", "Services", services.length],
                      ["target-groups", "Target groups", targetGroups.length],
                      ["load-balancers", "Load balancers", loadBalancers.length],
                      ["route-53", "Route 53", dnsRecords.length],
                    ] as const
                  ).map(([id, label, count]) => (
                    <button
                      key={id}
                      type="button"
                      role="tab"
                      aria-selected={section === id}
                      className={`section-tab${section === id ? " active" : ""}`}
                      onClick={() =>
                        setSectionByCluster((prev) => ({ ...prev, [cluster]: id }))
                      }
                    >
                      {label}
                      <em>{count}</em>
                    </button>
                  ))}
                </div>

                {section === "services" && (
                  <>
                    <div className="service-tabs" role="tablist" aria-label={`${cluster} services`}>
                      {ordered.map((item) => {
                        const key = serviceKey(item);
                        const active = selected && serviceKey(selected) === key;
                        return (
                          <button
                            key={key}
                            type="button"
                            role="tab"
                            aria-selected={active}
                            className={`service-tab ${item.status.toLowerCase()}${active ? " active" : ""}`}
                            onClick={() => setSelectedKey(key)}
                          >
                            <StatusLight light={serviceLight(item)} />
                            <span>{item.service}</span>
                          </button>
                        );
                      })}
                    </div>

                    {selected && (
                      <div className="service-tab-panel" role="tabpanel">
                        <div className="service-tab-panel-head">
                          <h3>{selected.service}</h3>
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
              </section>
            );
          })
        )}

        <footer className="footer">
          ECS Health Report v{report.version} · Services, target groups, load balancers, and Route 53
        </footer>
      </main>
    </div>
  );
}
