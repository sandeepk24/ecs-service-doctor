import type { ServiceResult, Topology } from "../types";
import { statusLabel } from "../utils";
import { StatusBadge } from "./StatusBadge";
import { TopologyDiagram } from "./TopologyDiagram";

interface Props {
  item: ServiceResult;
}

function CheckRow({
  label,
  status,
  message,
}: {
  label: string;
  status?: string;
  message?: string;
}) {
  if (!message) return null;
  return (
    <div className="check-row">
      <div className="check-label">{label}</div>
      <div className="check-value">
        {status && <StatusBadge status={status} />}
        <span>{message}</span>
      </div>
    </div>
  );
}

export function ServiceCard({ item }: Props) {
  const checks = item.checks ?? {};
  const events = checks.recent_events?.events ?? [];
  const images = checks.task_definition?.container_images ?? [];
  const connectivity = checks.connectivity as Topology | undefined;

  return (
    <article className={`service-card ${item.status.toLowerCase()}`}>
      <header className="service-head">
        <div>
          <h3>{item.service}</h3>
          {item.critical && <span className="critical-tag">Critical</span>}
        </div>
        <StatusBadge status={item.status} label={statusLabel(item.status)} />
      </header>

      {item.error ? (
        <div className="service-error">{item.error}</div>
      ) : (
        <>
          <div className="meta-grid">
            {item.task_definition && (
              <div>
                <span className="mini-label">Task definition</span>
                <code>{item.task_definition}</code>
              </div>
            )}
            {item.launch_type && (
              <div>
                <span className="mini-label">Launch</span>
                <strong>{item.launch_type}</strong>
              </div>
            )}
            {item.platform_version && (
              <div>
                <span className="mini-label">Platform</span>
                <strong>{item.platform_version}</strong>
              </div>
            )}
          </div>

          <div className="checks">
            <CheckRow
              label="Tasks"
              status={checks.task_counts?.status}
              message={checks.task_counts?.message}
            />
            <CheckRow
              label="Deployment"
              status={checks.deployments?.status}
              message={checks.deployments?.message}
            />
            <CheckRow
              label="Load balancer"
              status={checks.target_group_health?.status}
              message={checks.target_group_health?.message}
            />
          </div>

          {connectivity?.nodes?.length ? (
            <TopologyDiagram topology={connectivity} />
          ) : null}

          {images.length > 0 && (
            <div className="images">
              {images.map((image) => (
                <div key={image.container} className="image-row">
                  <span className="mini-label">{image.container}</span>
                  <code>{image.image}</code>
                </div>
              ))}
            </div>
          )}

          {events.length > 0 && (
            <div className="events">
              <h4>Recent events</h4>
              <ul>
                {events.slice(0, 8).map((event, index) => (
                  <li key={index}>{event.message}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </article>
  );
}
