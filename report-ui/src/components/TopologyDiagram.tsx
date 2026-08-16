export interface TopologyNode {
  id: string;
  type: string;
  label: string;
  detail?: string;
  dns_name?: string;
  vpc_id?: string;
  availability_zones?: string[];
}

export interface TopologyEdge {
  from: string;
  to: string;
  label?: string;
}

export interface HostHeaderRule {
  priority?: string;
  hosts?: string[];
  target_groups?: string[];
  target_group_arns?: string[];
  action?: string;
}

export interface LoadBalancerListener {
  port?: number;
  protocol?: string;
  ssl_policy?: string;
  default_actions?: string[];
  certificates?: string[];
  host_header_rules?: HostHeaderRule[];
}

export interface LoadBalancerDetail {
  arn?: string;
  type?: string;
  lb_type?: string;
  name?: string;
  dns_name?: string;
  hosted_zone_id?: string;
  scheme?: string;
  state?: string;
  vpc_id?: string;
  ip_address_type?: string;
  availability_zones?: string[];
  subnets?: string[];
  security_groups?: string[];
  listeners?: LoadBalancerListener[];
  dns_records?: DnsRecordDetail[];
}

export interface DnsRecordDetail {
  name?: string;
  type?: string;
  alias?: boolean;
  zone_name?: string;
  zone_id?: string;
  target?: string;
  load_balancer?: string;
  load_balancer_dns?: string;
}

export interface Topology {
  status?: string;
  summary?: string;
  entrypoint?: string;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  notes?: string[];
  mermaid?: string;
  load_balancers?: LoadBalancerDetail[];
  dns_records?: DnsRecordDetail[];
}

const TYPE_LABELS: Record<string, string> = {
  route53: "Route 53",
  alb: "ALB",
  nlb: "NLB",
  target_group: "Target Group",
  ecs_service: "ECS",
  rds: "RDS",
  redis: "ElastiCache",
  docdb: "DocumentDB",
  dynamodb: "DynamoDB",
  cloud_map: "Cloud Map",
  ecr: "ECR",
  internet: "Internet",
  bedrock: "Bedrock",
  s3: "S3",
  sqs: "SQS",
  sns: "SNS",
  opensearch: "OpenSearch",
  msk: "MSK",
  backend: "Backend",
};

const TYPE_CLASS: Record<string, string> = {
  route53: "route53",
  alb: "load-balancer",
  nlb: "load-balancer",
  target_group: "target-group",
  ecs_service: "ecs",
  rds: "database",
  redis: "database",
  docdb: "database",
  dynamodb: "database",
  bedrock: "database",
  s3: "ecr",
  sqs: "discovery",
  sns: "discovery",
  opensearch: "database",
  msk: "database",
  cloud_map: "discovery",
  ecr: "ecr",
  internet: "internet",
  backend: "database",
};

const TRAFFIC_RANK: Record<string, number> = {
  internet: 0,
  route53: 1,
  alb: 2,
  nlb: 2,
  target_group: 3,
  cloud_map: 3,
  ecs_service: 4,
};

const BACKEND_TYPES = new Set([
  "rds",
  "redis",
  "docdb",
  "dynamodb",
  "bedrock",
  "s3",
  "sqs",
  "sns",
  "opensearch",
  "msk",
  "backend",
]);

interface Props {
  topology: Topology;
}

function nodesByType(topology: Topology, types: string[]) {
  return topology.nodes.filter((node) => types.includes(node.type));
}

function laneEdges(topology: Topology, nodeIds: Set<string>) {
  return topology.edges.filter(
    (edge) => nodeIds.has(edge.from) && nodeIds.has(edge.to)
  );
}

function NodeCard({ node }: { node: TopologyNode }) {
  return (
    <div className={`topology-node ${TYPE_CLASS[node.type] ?? "backend"}`}>
      <span className="topology-type">{TYPE_LABELS[node.type] ?? node.type}</span>
      <strong>{node.label}</strong>
      {node.detail && <span className="topology-detail">{node.detail}</span>}
      {node.dns_name && <span className="topology-detail">{node.dns_name}</span>}
    </div>
  );
}

function EdgeList({
  edges,
  topology,
}: {
  edges: TopologyEdge[];
  topology: Topology;
}) {
  if (!edges.length) return null;
  const labels = new Map(topology.nodes.map((node) => [node.id, node.label]));
  return (
    <ul className="topology-edge-list">
      {edges.map((edge) => (
        <li key={`${edge.from}-${edge.to}-${edge.label ?? ""}`}>
          <strong>{labels.get(edge.from) ?? edge.from}</strong>
          <span>{edge.label ?? "→"}</span>
          <strong>{labels.get(edge.to) ?? edge.to}</strong>
        </li>
      ))}
    </ul>
  );
}

export function TopologyDiagram({ topology }: Props) {
  if (!topology.nodes.length) {
    return null;
  }

  const trafficNodes = [...topology.nodes]
    .filter((node) => node.type in TRAFFIC_RANK)
    .sort((a, b) => TRAFFIC_RANK[a.type] - TRAFFIC_RANK[b.type]);
  const backendNodes = nodesByType(topology, [...BACKEND_TYPES]);
  const imageNodes = nodesByType(topology, ["ecr"]);

  const trafficIds = new Set(trafficNodes.map((node) => node.id));
  const backendIds = new Set([
    ...backendNodes.map((node) => node.id),
    ...topology.nodes.filter((node) => node.type === "ecs_service").map((n) => n.id),
  ]);
  const imageIds = new Set([
    ...imageNodes.map((node) => node.id),
    ...topology.nodes.filter((node) => node.type === "ecs_service").map((n) => n.id),
  ]);

  const ranks = [...new Set(trafficNodes.map((node) => TRAFFIC_RANK[node.type]))].sort(
    (a, b) => a - b
  );

  return (
    <div className="topology">
      <div className="topology-head">
        <h4>Connectivity</h4>
        {topology.summary && <span className="topology-summary">{topology.summary}</span>}
      </div>

      <div className="topology-lane">
        <h5>How traffic reaches this service</h5>
        <div className="topology-columns">
          {ranks.map((rank, index) => (
            <div key={rank} className="topology-column-wrap">
              <div className="topology-column">
                {trafficNodes
                  .filter((node) => TRAFFIC_RANK[node.type] === rank)
                  .map((node) => (
                    <NodeCard key={node.id} node={node} />
                  ))}
              </div>
              {index < ranks.length - 1 && (
                <div className="topology-arrow">
                  <span>→</span>
                </div>
              )}
            </div>
          ))}
        </div>
        <EdgeList edges={laneEdges(topology, trafficIds)} topology={topology} />
      </div>

      {backendNodes.length > 0 && (
        <div className="topology-lane">
          <h5>What this service calls</h5>
          <div className="topology-flow">
            {nodesByType(topology, ["ecs_service"]).map((node) => (
              <NodeCard key={node.id} node={node} />
            ))}
            <div className="topology-arrow">
              <span>connects</span>
            </div>
            {backendNodes.map((node) => (
              <NodeCard key={node.id} node={node} />
            ))}
          </div>
          <EdgeList edges={laneEdges(topology, backendIds)} topology={topology} />
        </div>
      )}

      {imageNodes.length > 0 && (
        <div className="topology-lane">
          <h5>Container image</h5>
          <div className="topology-flow">
            {imageNodes.map((node) => (
              <NodeCard key={node.id} node={node} />
            ))}
            <div className="topology-arrow">
              <span>pulls image</span>
            </div>
            {nodesByType(topology, ["ecs_service"]).map((node) => (
              <NodeCard key={node.id} node={node} />
            ))}
          </div>
        </div>
      )}

      {topology.notes?.length ? (
        <ul className="topology-notes">
          {topology.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
