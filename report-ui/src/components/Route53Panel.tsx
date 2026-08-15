import type { ServiceResult } from "../types";
import type { LoadBalancerDetail, Topology, TopologyNode } from "./TopologyDiagram";

export interface DnsRecord {
  name?: string;
  type?: string;
  alias?: boolean;
  zone_name?: string;
  zone_id?: string;
  target?: string;
  load_balancer?: string;
  load_balancer_dns?: string;
}

export interface ClusterDnsRecord extends DnsRecord {
  key: string;
  services: string[];
}

function recordName(record: DnsRecord | TopologyNode): string | undefined {
  if ("label" in record && record.label) return record.label;
  return record.name;
}

export function collectClusterDnsRecords(
  services: ServiceResult[],
): ClusterDnsRecord[] {
  const map = new Map<string, ClusterDnsRecord>();

  const add = (record: DnsRecord, service: string) => {
    const name = record.name;
    if (!name) return;
    const key = `${name}::${record.load_balancer ?? record.target ?? ""}`;
    const existing = map.get(key);
    if (existing) {
      if (!existing.services.includes(service)) existing.services.push(service);
      return;
    }
    map.set(key, { ...record, name, key, services: [service] });
  };

  for (const item of services) {
    const connectivity = item.checks?.connectivity as Topology | undefined;
    for (const record of connectivity?.dns_records ?? []) {
      add(record, item.service);
    }
    for (const lb of connectivity?.load_balancers ?? []) {
      for (const record of lb.dns_records ?? []) {
        add(
          {
            ...record,
            load_balancer: record.load_balancer ?? lb.name,
            load_balancer_dns: record.load_balancer_dns ?? lb.dns_name,
            target: record.target ?? lb.dns_name,
          },
          item.service,
        );
      }
    }
    for (const node of connectivity?.nodes ?? []) {
      if (node.type !== "route53") continue;
      const name = recordName(node);
      if (!name) continue;
      const already = [...map.values()].some((entry) => entry.name === name);
      if (already) continue;
      add({ name, type: "A", alias: true }, item.service);
    }
  }

  return [...map.values()].sort((a, b) =>
    (a.name ?? "").localeCompare(b.name ?? ""),
  );
}

interface Props {
  records: ClusterDnsRecord[];
}

export function Route53Panel({ records }: Props) {
  if (!records.length) {
    return (
      <div className="empty-panel">
        No Route 53 records point at load balancers in this cluster. Records
        show up when an alias or CNAME targets the ALB/NLB DNS name.
      </div>
    );
  }

  return (
    <div className="dns-panel">
      <div className="dns-panel-head">
        <h4>Route 53</h4>
        <span>
          {records.length} record{records.length === 1 ? "" : "s"} pointing at
          cluster load balancers
        </span>
      </div>
      <ul className="dns-list">
        {records.map((record) => (
          <li key={record.key} className="dns-card">
            <header>
              <span className="dns-kind">
                {record.alias ? "Alias" : "Record"} {record.type ?? "A"}
              </span>
              <strong>{record.name}</strong>
            </header>
            <dl className="lb-facts">
              {record.zone_name && (
                <>
                  <dt>Zone</dt>
                  <dd>
                    <code>{record.zone_name}</code>
                  </dd>
                </>
              )}
              {record.load_balancer && (
                <>
                  <dt>Load balancer</dt>
                  <dd>{record.load_balancer}</dd>
                </>
              )}
              {(record.target || record.load_balancer_dns) && (
                <>
                  <dt>Target</dt>
                  <dd>
                    <code>{record.target || record.load_balancer_dns}</code>
                  </dd>
                </>
              )}
              {record.services.length > 0 && (
                <>
                  <dt>Services</dt>
                  <dd>{record.services.join(", ")}</dd>
                </>
              )}
            </dl>
          </li>
        ))}
      </ul>
    </div>
  );
}
