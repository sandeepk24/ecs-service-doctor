import type { EcsReport } from "../types";

interface Props {
  summary: EcsReport["summary"];
}

const cards = [
  { key: "total_services", label: "Services", tone: "neutral" },
  { key: "passed", label: "Healthy", tone: "pass" },
  { key: "warnings", label: "Warnings", tone: "warn" },
  { key: "failed", label: "Failed", tone: "fail" },
] as const;

export function SummaryGrid({ summary }: Props) {
  return (
    <section className="summary-grid">
      {cards.map(({ key, label, tone }) => (
        <article key={key} className={`summary-card ${tone}`}>
          <div className="summary-num">{summary[key]}</div>
          <div className="summary-label">{label}</div>
        </article>
      ))}
    </section>
  );
}
