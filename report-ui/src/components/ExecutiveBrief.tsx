import type { EcsReport, ServiceResult } from "../types";
import {
  attentionItems,
  executiveHeadline,
  executiveSubhead,
  serviceSnapshot,
  statusLabel,
} from "../utils";
import { StatusBadge } from "./StatusBadge";

interface Props {
  report: EcsReport;
  onSelect: (serviceKey: string) => void;
}

function serviceKey(item: ServiceResult) {
  return `${item.cluster}::${item.service}`;
}

export function ExecutiveBrief({ report, onSelect }: Props) {
  const overall = report.account_check.status === "FAIL"
    ? "FAIL"
    : report.summary.failed > 0
      ? "FAIL"
      : report.summary.warnings > 0
        ? "WARN"
        : "PASS";
  const issues = attentionItems(report.results);

  return (
    <section className={`brief brief-${overall.toLowerCase()}`}>
      <div className="brief-copy">
        <p className="brief-kicker">Executive snapshot</p>
        <h2>{executiveHeadline(report)}</h2>
        <p className="brief-sub">{executiveSubhead(report)}</p>
      </div>

      {issues.length > 0 && (
        <ul className="attention-list">
          {issues.map((item) => (
            <li key={serviceKey(item)}>
              <button
                type="button"
                className="attention-item"
                onClick={() => onSelect(serviceKey(item))}
              >
                <StatusBadge status={item.status} label={statusLabel(item.status)} />
                <div>
                  <strong>
                    {item.service}
                    {item.critical ? " · Critical" : ""}
                  </strong>
                  <span>{serviceSnapshot(item)}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
