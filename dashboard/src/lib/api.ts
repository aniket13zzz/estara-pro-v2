// Small client for the backend API (same origin via the Next.js /api rewrite).
// State-changing requests send the CSRF token from the readable `estara_csrf` cookie.

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
  ) {
    super(message);
  }
}

function csrfToken(): string {
  const match = document.cookie.split("; ").find((c) => c.startsWith("estara_csrf="));
  return match ? decodeURIComponent(match.slice("estara_csrf=".length)) : "";
}

function describe(status: number, body: unknown): { message: string; code?: string } {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return { message: detail };
    if (detail && typeof detail === "object" && "message" in detail) {
      const d = detail as { message: string; code?: string };
      return { message: d.message, code: d.code };
    }
    if (Array.isArray(detail)) return { message: "Some fields are invalid." };
  }
  return { message: `Request failed (${status})` };
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (method !== "GET" && method !== "HEAD") headers.set("X-CSRF-Token", csrfToken());
  if (init.body && typeof init.body === "string") headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  } catch {
    throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
  }
  if (res.status === 401 && typeof window !== "undefined" && !path.endsWith("/auth/login")) {
    // Session expired: a full reload to the login page also drops any stale client state.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  }
  const text = await res.text();
  const body: unknown = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const { message, code } = describe(res.status, body);
    throw new ApiError(res.status, message, code);
  }
  return body as T;
}

export type Role = "client_admin" | "sales_staff";
export type Me = { id: string; email: string; name: string; role: Role };

export type Batch = {
  id: string;
  filename: string | null;
  entity_type: string;
  status: "uploaded" | "validated" | "approved" | "rejected" | "failed" | "archived";
  row_count: number;
  error_count: number;
  warning_count: number;
  summary: {
    entities?: Record<
      string,
      { rows: number; new: number; changed: number; unchanged: number; errors: number; warnings: number }
    >;
    title?: string;
    doc_type?: string;
    chunks?: number;
  };
  created_at: string;
  approved_at: string | null;
  archived_at: string | null;
  rejected_reason: string | null;
};

export type Finding = {
  entity: string | null;
  row_number: number | null;
  column: string | null;
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
};

export type PreviewRow = {
  entity: string;
  line: number;
  change: "new" | "changed" | "unchanged";
  changed_fields: string[];
  valid: boolean;
  values: Record<string, unknown>;
};

export type Preview = {
  batch: Batch;
  findings: Finding[];
  rows: PreviewRow[];
  total_rows: number;
  can_approve: boolean;
};

export type Alert = { id: string; kind: string; severity: string; message: string; updated_at: string };

export type Freshness = {
  stale_after_days: number;
  checked_at: string;
  entities: {
    entity: string;
    label: string;
    approved: number;
    stale: number;
    pending: number;
    oldest_approved_at: string | null;
    next_stale_at: string | null;
  }[];
  alerts: Alert[];
};

export type UnitTypeRow = {
  code: string;
  name: string;
  bhk: number;
  property_type: string;
  carpet_area: string | null;
  price: string | null;
  units: number;
  available: number;
  approved_at: string;
  stale: boolean;
  stale_reason: string | null;
};

export type ProjectRow = {
  code: string;
  name: string;
  city: string;
  locality: string | null;
  status: "approved" | "pending";
  pending_versions: number;
  possession?: string | null;
  version?: number;
  approved_at?: string;
  stale?: boolean;
  stale_reason?: string | null;
  unit_types: UnitTypeRow[];
  units: { total: number; available: number; stale: number };
};

export const ENTITY_LABELS: Record<string, string> = {
  projects: "Projects",
  unit_types: "Unit types",
  units: "Units",
  price_lists: "Price lists",
  offers: "Offers",
  payment_plans: "Payment plans",
  amenities: "Amenities",
  inventory: "Workbook",
  document: "Document",
};

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------- leads, calls, visits

export type LeadState =
  | "new_enquiry"
  | "engaged"
  | "qualified"
  | "visit_requested"
  | "visit_confirmed"
  | "handed_off"
  | "nurturing"
  | "won"
  | "lost";

export type LeadRow = {
  id: string;
  name: string | null;
  phone: string | null;
  source: string;
  state: LeadState;
  score: number | null;
  band: string | null;
  budget: string | null;
  bhk: number[];
  locations: string[];
  last_contact_at: string | null;
  next_action: string | null;
  created_at: string;
};

export type VisitRow = {
  id: string;
  lead_id?: string;
  project: string | null;
  project_code: string | null;
  date: string;
  slot: string;
  state: "requested" | "confirmed" | "completed" | "rescheduled" | "cancelled" | "no_show";
  visitor_name: string | null;
  notes: string | null;
  requested_via: string;
  confirmed_at: string | null;
  customer_name?: string | null;
  phone?: string | null;
};

export type CallRow = {
  id: string;
  vapi_call_id: string;
  caller_phone: string | null;
  customer_id: string | null;
  language: string | null;
  started_at: string | null;
  duration_seconds: number | null;
  outcome: string | null;
  ended_reason: string | null;
  summary: string | null;
  recording_url: string | null;
  cost_usd: number | null;
  /** Native voice: numbers the agent said that no approved data or caller words contained. */
  unverified_numbers: string[];
  transcript?: string | null;
  lead_id?: string | null;
};

export type LeadDetail = {
  id: string;
  customer: { name: string | null; phone: string | null; preferred_language: string | null };
  state: LeadState;
  score: number | null;
  band: string | null;
  score_reason: string | null;
  source: string;
  requirements: {
    budget?: string | null;
    locations?: string[];
    property_type?: string | null;
    bhk?: number[];
    timeline_months?: number | null;
    loan_needed?: boolean | null;
    purpose?: string | null;
  };
  matches: { rank: number; project: string; project_code: string; reason: string | null }[];
  objections: { type: string; text: string; language: string; at: string }[];
  visits: VisitRow[];
  handoffs: {
    id: string;
    reason: string;
    status: string;
    assigned_email: string | null;
    created_at: string;
    next_action: string | null;
  }[];
  timeline: { at: string; channel: string; from: string; language: string; text: string }[];
  calls: CallRow[];
};

export const LEAD_STATE_LABELS: Record<LeadState, string> = {
  new_enquiry: "New enquiry",
  engaged: "Engaged",
  qualified: "Qualified",
  visit_requested: "Visit requested",
  visit_confirmed: "Visit confirmed",
  handed_off: "With sales team",
  nurturing: "Follow up later",
  won: "Won",
  lost: "Lost",
};

export function formatDate(value: string): string {
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m ? `${m} min ${s} s` : `${s} s`;
}
