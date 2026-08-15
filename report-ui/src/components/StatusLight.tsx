interface Props {
  light: "green" | "red";
  label?: string;
  large?: boolean;
}

export function StatusLight({ light, label, large }: Props) {
  const text =
    label ?? (light === "green" ? "Up · HTTP 200" : "Down · not HTTP 200");
  return (
    <span className={`status-light-wrap${large ? " large" : ""}`} title={text}>
      <span className={`status-light ${light}`} />
      {label !== undefined && <span className="status-light-text">{text}</span>}
    </span>
  );
}
