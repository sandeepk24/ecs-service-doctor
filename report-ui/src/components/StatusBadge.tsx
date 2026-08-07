import type { Status } from "../types";

interface Props {
  status: Status | string;
  label?: string;
  large?: boolean;
}

export function StatusBadge({ status, label, large }: Props) {
  const css = status.toLowerCase();
  return (
    <span className={`status-badge ${css}${large ? " large" : ""}`}>
      {label ?? status}
    </span>
  );
}
