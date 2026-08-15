import type { Status } from "../types";
import { toneFromStatus } from "../utils";

interface Props {
  status: Status | string;
  label?: string;
  large?: boolean;
}

export function StatusBadge({ status, label, large }: Props) {
  const tone = toneFromStatus(status);
  return (
    <span className={`status-badge ${tone}${large ? " large" : ""}`}>
      {label ?? status}
    </span>
  );
}
