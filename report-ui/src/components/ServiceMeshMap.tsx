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

  return (
    <section className="mesh">
      <div className="mesh-head">
        <div>
          <h3>Service connections</h3>
          <p>
            Green light = up (HTTP 200). Red light = not HTTP 200. Use this map
            after the service tabs to see who can reach whom.
          </p>
        </div>
        {mesh.summary && <span className="mesh-summary">{mesh.summary}</span>}
      </div>

      <div className="mesh-legend">
        <StatusLight light="green" label="Up · HTTP 200" />
        <StatusLight light="red" label="Down · not HTTP 200" />
      </div>

      <div className="mesh-node-row">
        {nodes.map((node) => (
          <button
            key={node.id}
            type="button"
            className={`mesh-chip ${node.light}`}
            onClick={() => onSelect(node.id)}
          >
            <StatusLight light={node.light} />
            <span>
              <strong>{node.service}</strong>
              <em>
                {node.light === "green"
                  ? "HTTP 200"
                  : node.http_status
                    ? `HTTP ${node.http_status}`
                    : "Not 200"}
              </em>
            </span>
          </button>
        ))}
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
