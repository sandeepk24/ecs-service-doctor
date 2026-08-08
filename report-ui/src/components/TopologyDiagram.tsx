export interface TopologyNode {
  id: string;
  type: string;
  label: string;
  detail?: string;
}

export interface TopologyEdge {
  from: string;
  to: string;
  label?: string;
}

export interface Topology {
  status?: string;
  summary?: string;
  entrypoint?: string;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  notes?: string[];
  mermaid?: string;
}

const TYPE_LABELS: Record<string, string> = {
  route53: "Route 53",
  alb: "ALB",
  nlb: "NLB",
  ecs_service: "ECS",
  rds: "RDS",
  redis: "ElastiCache",
  docdb: "DocumentDB",
  dynamodb: "DynamoDB",
  cloud_map: "Cloud Map",
  ecr: "ECR",
  internet: "Internet",
  backend: "Backend",
};

const TYPE_CLASS: Record<string, string> = {
  route53: "route53",
  alb: "load-balancer",
  nlb: "load-balancer",
  ecs_service: "ecs",
  rds: "database",
  redis: "database",
  docdb: "database",
  dynamodb: "database",
  cloud_map: "discovery",
  ecr: "ecr",
  internet: "internet",
  backend: "database",
};

interface Props {
  topology: Topology;
}

function orderedNodes(topology: Topology): TopologyNode[] {
  const nodeMap = new Map(topology.nodes.map((node) => [node.id, node]));
  const seen = new Set<string>();
  const ordered: TopologyNode[] = [];

  const walk = (nodeId: string) => {
    if (seen.has(nodeId)) {
      return;
    }
    seen.add(nodeId);
    const node = nodeMap.get(nodeId);
    if (!node) {
      return;
    }
    ordered.push(node);
    for (const edge of topology.edges) {
      if (edge.from === nodeId) {
        walk(edge.to);
      }
    }
  };

  if (topology.entrypoint) {
    walk(topology.entrypoint);
  }

  for (const node of topology.nodes) {
    if (!seen.has(node.id)) {
      ordered.push(node);
    }
  }

  return ordered;
}

function edgeLabel(topology: Topology, from: string, to: string) {
  return topology.edges.find((edge) => edge.from === from && edge.to === to)?.label;
}

export function TopologyDiagram({ topology }: Props) {
  if (!topology.nodes.length) {
    return null;
  }

  const nodes = orderedNodes(topology);

  return (
    <div className="topology">
      <div className="topology-head">
        <h4>Connectivity</h4>
        {topology.summary && <span className="topology-summary">{topology.summary}</span>}
      </div>

      <div className="topology-flow">
        {nodes.map((node, index) => (
          <div key={node.id} className="topology-step">
            <div className={`topology-node ${TYPE_CLASS[node.type] ?? "backend"}`}>
              <span className="topology-type">
                {TYPE_LABELS[node.type] ?? node.type}
              </span>
              <strong>{node.label}</strong>
              {node.detail && <span className="topology-detail">{node.detail}</span>}
            </div>
            {index < nodes.length - 1 && (
              <div className="topology-arrow">
                <span>
                  {edgeLabel(topology, node.id, nodes[index + 1].id) ?? "→"}
                </span>
              </div>
            )}
          </div>
        ))}
      </div>

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
