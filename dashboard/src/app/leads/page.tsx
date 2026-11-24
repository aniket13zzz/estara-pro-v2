"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge, Empty, ErrorBox, Loading } from "@/components/Status";
import { api, formatDateTime, LEAD_STATE_LABELS, type LeadRow, type LeadState } from "@/lib/api";

type State = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; leads: LeadRow[] };

const FILTERS: (LeadState | "")[] = ["", "visit_requested", "visit_confirmed", "qualified", "handed_off", "nurturing"];

function needs(lead: LeadRow): string {
  return [lead.bhk.length ? `${lead.bhk.join("/")} BHK` : null, lead.locations.join(", ") || null, lead.budget]
    .filter(Boolean)
    .join(" · ");
}

export default function LeadsPage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [filter, setFilter] = useState<LeadState | "">("");

  const load = useCallback(() => {
    const query = filter ? `?state=${filter}` : "";
    api<LeadRow[]>(`/api/v1/leads${query}`)
      .then((leads) => setState({ kind: "ok", leads }))
      .catch((e: unknown) =>
        setState({ kind: "error", message: e instanceof Error ? e.message : "Could not load leads." }),
      );
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <h1>Leads</h1>
      <p className="muted small">Every enquiry the agent handled, newest activity first.</p>
      <div className="actions">
        <label>
          Show{" "}
          <select
            value={filter}
            onChange={(e) => {
              setState({ kind: "loading" });
              setFilter(e.target.value as LeadState | "");
            }}
            aria-label="Filter by status"
          >
            {FILTERS.map((f) => (
              <option key={f || "all"} value={f}>
                {f ? LEAD_STATE_LABELS[f] : "All leads"}
              </option>
            ))}
          </select>
        </label>
      </div>
      {state.kind === "loading" && <Loading />}
      {state.kind === "error" && <ErrorBox message={state.message} onRetry={load} />}
      {state.kind === "ok" && state.leads.length === 0 && (
        <section className="card">
          <Empty>No leads yet. They appear here as soon as someone calls or messages the agent.</Empty>
        </section>
      )}
      {state.kind === "ok" && state.leads.length > 0 && (
        <section className="card">
          <div className="table-wrap">
            <table aria-label="Leads">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Phone</th>
                  <th>Looking for</th>
                  <th>Status</th>
                  <th className="num">Score</th>
                  <th>Last contact</th>
                  <th>Next action</th>
                </tr>
              </thead>
              <tbody>
                {state.leads.map((l) => (
                  <tr key={l.id}>
                    <td>
                      <Link href={`/leads/${l.id}`}>{l.name ?? "Unknown caller"}</Link>
                      <div className="muted small">via {l.source.replace("_", " ")}</div>
                    </td>
                    <td>{l.phone ?? "—"}</td>
                    <td>{needs(l) || <span className="muted">not known yet</span>}</td>
                    <td>
                      <Badge value={l.state} />
                    </td>
                    <td className="num">
                      {l.score ?? "—"} {l.band && <Badge value={l.band} />}
                    </td>
                    <td>{formatDateTime(l.last_contact_at)}</td>
                    <td className="small">{l.next_action ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
