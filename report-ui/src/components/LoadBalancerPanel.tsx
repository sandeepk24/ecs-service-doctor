import type { ServiceResult } from "../types";
import type { LoadBalancerDetail, Topology } from "./TopologyDiagram";

export interface ClusterLoadBalancer extends LoadBalancerDetail {
  key: string;
  services: string[];
}

export function collectClusterLoadBalancers(
  services: ServiceResult[],
): ClusterLoadBalancer[] {
  const map = new Map<string, ClusterLoadBalancer>();
  for (const item of services) {
    const connectivity = item.checks?.connectivity as Topology | undefined;
    for (const lb of connectivity?.load_balancers ?? []) {
      const key = lb.arn ?? lb.name ?? `${item.service}-lb`;
      const existing = map.get(key);
      if (existing) {
        if (!existing.services.includes(item.service)) {
          existing.services.push(item.service);
        }
        continue;
      }
      map.set(key, { ...lb, key, services: [item.service] });
    }
  }
  return [...map.values()];
}

interface Props {
  loadBalancers: ClusterLoadBalancer[];
}

export function LoadBalancerPanel({ loadBalancers }: Props) {
  if (!loadBalancers.length) {
    return (
      <div className="empty-panel">
        No load balancers are attached to services in this cluster.
      </div>
    );
  }

  return (
    <div className="lb-panel">
      <div className="lb-panel-head">
        <h4>Load balancers</h4>
        <span>
          {loadBalancers.length} attached
        </span>
      </div>
      <div className="lb-list">
        {loadBalancers.map((lb) => {
          const kind = (lb.lb_type || lb.type || "application").toLowerCase();
          const title = kind === "network" || lb.type === "nlb" ? "NLB" : "ALB";
          return (
            <article key={lb.key} className="lb-card">
              <header>
                <span className="lb-kind">{title}</span>
                <strong>{lb.name ?? "load balancer"}</strong>
                {lb.state && <span className={`lb-state ${lb.state}`}>{lb.state}</span>}
              </header>
              {lb.services.length > 0 && (
                <p className="lb-services">
                  Services: {lb.services.join(", ")}
                </p>
              )}
              <dl className="lb-facts">
                {lb.dns_name && (
                  <>
                    <dt>DNS</dt>
                    <dd>
                      <code>{lb.dns_name}</code>
                    </dd>
                  </>
                )}
                {!!lb.dns_records?.length && (
                  <>
                    <dt>Route 53</dt>
                    <dd>
                      {lb.dns_records
                        .map((record) => record.name)
                        .filter(Boolean)
                        .join(", ")}
                    </dd>
                  </>
                )}
                {lb.scheme && (
                  <>
                    <dt>Scheme</dt>
                    <dd>{lb.scheme}</dd>
                  </>
                )}
                {lb.vpc_id && (
                  <>
                    <dt>VPC</dt>
                    <dd>
                      <code>{lb.vpc_id}</code>
                    </dd>
                  </>
                )}
                {lb.ip_address_type && (
                  <>
                    <dt>IP type</dt>
                    <dd>{lb.ip_address_type}</dd>
                  </>
                )}
                {!!lb.availability_zones?.length && (
                  <>
                    <dt>AZs</dt>
                    <dd>{lb.availability_zones.join(", ")}</dd>
                  </>
                )}
                {!!lb.subnets?.length && (
                  <>
                    <dt>Subnets</dt>
                    <dd>
                      {lb.subnets.map((subnet) => (
                        <code key={subnet}>{subnet}</code>
                      ))}
                    </dd>
                  </>
                )}
                {!!lb.security_groups?.length && (
                  <>
                    <dt>Security groups</dt>
                    <dd>
                      {lb.security_groups.map((group) => (
                        <code key={group}>{group}</code>
                      ))}
                    </dd>
                  </>
                )}
                {lb.hosted_zone_id && (
                  <>
                    <dt>Hosted zone</dt>
                    <dd>
                      <code>{lb.hosted_zone_id}</code>
                    </dd>
                  </>
                )}
              </dl>
              {!!lb.listeners?.length && (
                <div className="lb-listeners">
                  <h5>Listeners</h5>
                  <ul>
                    {lb.listeners.map((listener, index) => (
                      <li key={`${listener.protocol}-${listener.port}-${index}`}>
                        <strong>
                          {listener.protocol}:{listener.port}
                        </strong>
                        <span>
                          {(listener.default_actions ?? []).join(" · ") ||
                            "no default action"}
                        </span>
                        {listener.ssl_policy && (
                          <span className="lb-ssl">{listener.ssl_policy}</span>
                        )}
                        {!!listener.host_header_rules?.length && (
                          <ul className="host-rules">
                            {listener.host_header_rules.map((rule, ruleIndex) => (
                              <li key={`${rule.priority ?? "rule"}-${ruleIndex}`}>
                                Host {(rule.hosts ?? []).join(", ")}
                                {rule.action ? ` · ${rule.action}` : ""}
                              </li>
                            ))}
                          </ul>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
