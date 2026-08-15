import type { ServiceMesh } from "../types";
import { StatusLight } from "./StatusLight";

interface Props {
  mesh: ServiceMesh;
  onSelect: (serviceKey: string) => void;
}

export function ServiceMeshMap({ mesh, onSelect }: Props) {
  const nodes = mesh.nodes ?? [];
  const edges = mesh.edges ?? [];
  if (!nodes.length) return null;

  const size = 420;
  const cx = size / 2;
  const cy = size / 2;
  const radius = nodes.length <= 2 ? 0 : 148;
  const positions = new Map(
    nodes.map((node, index) => {
      const angle = (2 * Math.PI * index) / nodes.length - Math.PI / 2;
      return [
        node.id,
        {
          x: cx + radius * Math.cos(angle),
          y: cy + radius * Math.sin(angle),
        },
      ] as const;
    })
  );

  return (
    <section className="mesh">
      <div className="mesh-head">
        <div>
          <h3>Service connections</h3>
          <p>
            Green light = up and returning HTTP 200. Red light = not HTTP 200.
            Lines show which services call each other.
          </p>
        </div>
        {mesh.summary && <span className="mesh-summary">{mesh.summary}</span>}
      </div>

      <div className="mesh-legend">
        <StatusLight light="green" label="Up · HTTP 200" />
        <StatusLight light="red" label="Down · not HTTP 200" />
      </div>

      <div className="mesh-canvas">
        <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Service connection map">
          {edges.map((edge) => {
            const from = positions.get(edge.from);
            const to = positions.get(edge.to);
            if (!from || !to) return null;
            return (
              <line
                key={`${edge.from}-${edge.to}`}
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                className={edge.ok ? "mesh-link ok" : "mesh-link blocked"}
              />
            );
          })}
        </svg>
        {nodes.map((node) => {
          const point = positions.get(node.id);
          if (!point) return null;
          return (
            <button
              key={node.id}
              type="button"
              className={`mesh-node ${node.light}`}
              style={{ left: point.x, top: point.y }}
              onClick={() => onSelect(node.id)}
            >
              <StatusLight light={node.light} />
              <strong>{node.service}</strong>
              <span>
                {node.light === "green"
                  ? "HTTP 200"
                  : node.http_status
                    ? `HTTP ${node.http_status}`
                    : "Not 200"}
              </span>
            </button>
          );
        })}
      </div>

      {edges.length > 0 && (
        <ul className="mesh-edges">
          {edges.map((edge) => (
            <li
              key={`${edge.from}-${edge.to}`}
              className={edge.ok ? "ok" : "blocked"}
            >
              <button type="button" onClick={() => onSelect(edge.to)}>
                <strong>
                  {edge.from_service} → {edge.to_service}
                </strong>
                <span>
                  {edge.ok
                    ? "Can connect — both services are up"
                    : "Blocked — destination is not HTTP 200"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
