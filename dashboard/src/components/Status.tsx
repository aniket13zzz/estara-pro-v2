import type { ReactNode } from "react";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="muted" role="status">
      {label}
    </p>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="notice notice-error" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button type="button" className="link-button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Notice({ kind, children }: { kind: "ok" | "warn" | "error"; children: ReactNode }) {
  return (
    <div className={`notice notice-${kind}`} role={kind === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

const BADGE: Record<string, string> = {
  validated: "warn",
  approved: "ok",
  failed: "error",
  rejected: "muted",
  archived: "muted",
  uploaded: "muted",
  pending: "warn",
  error: "error",
  warning: "warn",
  info: "muted",
  new: "ok",
  changed: "warn",
  unchanged: "muted",
  requested: "warn",
  confirmed: "ok",
  completed: "ok",
  rescheduled: "warn",
  cancelled: "muted",
  no_show: "error",
  visit_requested: "warn",
  visit_confirmed: "ok",
  qualified: "ok",
  handed_off: "warn",
  won: "ok",
  lost: "muted",
  hot: "error",
  warm: "warn",
  cold: "muted",
};

const LABEL: Record<string, string> = {
  validated: "awaiting approval",
  requested: "needs confirmation",
  no_show: "no-show",
  visit_requested: "visit requested",
  visit_confirmed: "visit confirmed",
  handed_off: "with sales team",
  new_enquiry: "new enquiry",
};

export function Badge({ value }: { value: string }) {
  return <span className={`badge badge-${BADGE[value] ?? "muted"}`}>{LABEL[value] ?? value}</span>;
}
