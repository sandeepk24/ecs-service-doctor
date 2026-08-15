import type { LoadBalancerDetail } from "./TopologyDiagram";

interface Props {
  loadBalancers?: LoadBalancerDetail[];
}

export function LoadBalancerPanel({ loadBalancers }: Props) {
  if (!loadBalancers?.length) {
    return null;
  }

  return (
    <div className="lb-panel">
      <div className="lb-panel-head">
        <h4>Load balancer details</h4>
        <span>{loadBalancers.length} attached</span>
      </div>
      <div className="lb-list">
        {loadBalancers.map((lb) => {
          const kind = (lb.lb_type || lb.type || "application").toLowerCase();
          const title = kind === "network" || lb.type === "nlb" ? "NLB" : "ALB";
          return (
            <article key={lb.arn ?? lb.name} className="lb-card">
              <header>
                <span className="lb-kind">{title}</span>
                <strong>{lb.name ?? "load balancer"}</strong>
                {lb.state && <span className={`lb-state ${lb.state}`}>{lb.state}</span>}
              </header>
              <dl className="lb-facts">
                {lb.dns_name && (
                  <>
                    <dt>DNS</dt>
                    <dd>
                      <code>{lb.dns_name}</code>
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
                          {(listener.default_actions ?? []).join(" · ") || "no default action"}
                        </span>
                        {listener.ssl_policy && (
                          <span className="lb-ssl">{listener.ssl_policy}</span>
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
