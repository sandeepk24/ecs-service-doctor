import type { EcsReport, ServiceResult } from "../types";
import {
  attentionItems,
  serviceLight,
  serviceSnapshot,
  statusLabel,
} from "../utils";
import { StatusBadge } from "./StatusBadge";
import { StatusLight } from "./StatusLight";

interface Props {
  report: EcsReport;
  onSelect: (serviceKey: string) => void;
}

function serviceKey(item: ServiceResult) {
  return `${item.cluster}::${item.service}`;
}

export function ExecutiveBrief({ report, onSelect }: Props) {
  const issues = attentionItems(report.results);
  if (!issues.length) {
    return null;
  }

  return (
    <section className="brief brief-fail">
      <div className="brief-copy">
        <p className="brief-kicker">Needs attention</p>
        <h2>
          {issues.length === 1
            ? "1 service needs a look"
            : `${issues.length} services need a look`}
        </h2>
      </div>
      <ul className="attention-list">
        {issues.map((item) => (
          <li key={serviceKey(item)}>
            <button
              type="button"
              className="attention-item"
              onClick={() => onSelect(serviceKey(item))}
            >
              <StatusLight light={serviceLight(item)} />
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
    </section>
  );
}
