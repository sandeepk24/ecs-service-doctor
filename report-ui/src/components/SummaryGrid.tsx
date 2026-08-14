import type { EcsReport } from "../types";

interface Props {
  summary: EcsReport["summary"];
}

const cards = [
  { key: "total_services", label: "Checked", tone: "neutral" },
  { key: "passed", label: "Healthy", tone: "pass" },
  { key: "warnings", label: "Attention", tone: "warn" },
  { key: "failed", label: "Unhealthy", tone: "fail" },
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
