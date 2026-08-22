import type { ReportCheck, ServiceResult, Status } from "../types";
import { formatTimestamp, statusLabel } from "../utils";
import { StatusBadge } from "./StatusBadge";

export interface CicdInfo {
  provider?: string;
  provider_label?: string;
  detected?: boolean;
  repository?: string;
  project_url?: string;
  branch?: string;
  commit?: string;
  commit_short?: string;
  pipeline_id?: string;
  build_number?: string;
  pipeline_url?: string;
  actor?: string;
  pipeline_status?: string;
  message?: string;
  status?: Status;
}

export interface EcsDeployment {
  id?: string;
  status?: string;
  rollout_state?: string;
  task_definition?: string;
  desired?: number;
  running?: number;
  pending?: number;
  failed_tasks?: number;
  created_at?: string;
  updated_at?: string;
}

export interface ClusterDeployment {
  key: string;
  service: string;
  serviceStatus?: Status;
  message?: string;
  status?: Status;
  cicd?: CicdInfo;
  deployments: EcsDeployment[];
}

export function collectClusterDeployments(
  services: ServiceResult[],
): ClusterDeployment[] {
  return services.map((item) => {
    const check = item.checks?.cicd as
      | (ReportCheck & {
          cicd?: CicdInfo;
          deployments?: EcsDeployment[];
        })
      | undefined;
    const legacy = item.checks?.deployments as
      | (ReportCheck & { deployments?: EcsDeployment[] })
      | undefined;
    return {
      key: `${item.cluster}::${item.service}`,
      service: item.service,
      serviceStatus: item.status,
      message: check?.message || legacy?.message,
      status: check?.status || legacy?.status,
      cicd: check?.cicd,
      deployments: check?.deployments?.length
        ? check.deployments
        : legacy?.deployments ?? [],
    };
  });
}

interface Props {
  items: ClusterDeployment[];
}

export function DeploymentsPanel({ items }: Props) {
  const withCi = items.filter((item) => item.cicd?.detected).length;

  if (!items.length) {
    return (
      <div className="empty-panel">No services in this cluster.</div>
    );
  }

  return (
    <div className="backend-groups">
      <div className="target-groups-head">
        <h4>CI/CD &amp; deployments</h4>
        <span className="target-groups-summary">
          {items.length} service{items.length === 1 ? "" : "s"}
          {withCi ? ` · ${withCi} with CI/CD metadata` : ""}
        </span>
      </div>
      {items.map((item) => {
        const cicd = item.cicd;
        return (
          <section key={item.key} className="backend-service">
            <header className="backend-service-head">
              <h5>{item.service}</h5>
              <StatusBadge
                status={item.status ?? "PASS"}
                label={statusLabel(item.status ?? "PASS")}
              />
            </header>
            {item.message && <p className="backend-message">{item.message}</p>}

            {cicd?.detected ? (
              <div className="cicd-card">
                <div className="target-group-title">
                  <strong>{cicd.provider_label || "CI/CD"}</strong>
                  {cicd.pipeline_status && (
                    <span className="cicd-pill">{cicd.pipeline_status}</span>
                  )}
                </div>
                <div className="target-group-meta">
                  {cicd.repository &&
                    (cicd.project_url ? (
                      <a href={cicd.project_url} target="_blank" rel="noreferrer">
                        {cicd.repository}
                      </a>
                    ) : (
                      <span>{cicd.repository}</span>
                    ))}
                  {cicd.branch && <span>branch {cicd.branch}</span>}
                  {cicd.commit_short && <span>commit {cicd.commit_short}</span>}
                  {cicd.build_number && <span>build #{cicd.build_number}</span>}
                  {cicd.actor && <span>by {cicd.actor}</span>}
                </div>
                {cicd.pipeline_url && (
                  <p className="backend-message">
                    <a href={cicd.pipeline_url} target="_blank" rel="noreferrer">
                      Open pipeline run
                    </a>
                  </p>
                )}
              </div>
            ) : (
              <p className="backend-message">
                No GitHub, GitLab, Bitbucket, CodeBuild, or other CI signals found
                in the task definition. Bake{" "}
                <code>GITHUB_*</code>, <code>CI_*</code>, or{" "}
                <code>BITBUCKET_*</code> env vars into the image build to track
                deployments here.
              </p>
            )}

            {item.deployments.length > 0 && (
              <ul className="target-group-list">
                {item.deployments.map((deployment) => (
                  <li
                    key={deployment.id || deployment.task_definition}
                    className={`target-group-item ${
                      deployment.rollout_state === "FAILED" ||
                      (deployment.failed_tasks ?? 0) > 0
                        ? "issue"
                        : deployment.rollout_state &&
                            deployment.rollout_state !== "COMPLETED"
                          ? "warn"
                          : "ok"
                    }`}
                  >
                    <div className="target-group-title">
                      <strong>
                        {deployment.status || "deployment"} ·{" "}
                        {deployment.task_definition || "task definition"}
                      </strong>
                      <StatusBadge
                        status={
                          deployment.rollout_state === "FAILED" ||
                          (deployment.failed_tasks ?? 0) > 0
                            ? "FAIL"
                            : deployment.rollout_state &&
                                deployment.rollout_state !== "COMPLETED"
                              ? "WARN"
                              : "PASS"
                        }
                        label={deployment.rollout_state || deployment.status || "OK"}
                      />
                    </div>
                    <div className="target-group-meta">
                      {deployment.desired != null && (
                        <span>
                          tasks {deployment.running ?? 0}/{deployment.desired}
                          {deployment.pending
                            ? ` · ${deployment.pending} pending`
                            : ""}
                        </span>
                      )}
                      {(deployment.failed_tasks ?? 0) > 0 && (
                        <span>{deployment.failed_tasks} failed</span>
                      )}
                      {deployment.created_at && (
                        <span>
                          started {formatTimestamp(deployment.created_at)}
                        </span>
                      )}
                      {deployment.updated_at && (
                        <span>
                          updated {formatTimestamp(deployment.updated_at)}
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
