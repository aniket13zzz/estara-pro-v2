"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge, Empty, ErrorBox, Loading } from "@/components/Status";
import { VisitActions } from "@/components/VisitActions";
import { api, formatDate, type VisitRow } from "@/lib/api";

type State = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; visits: VisitRow[] };

export default function VisitsPage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [onlyOpen, setOnlyOpen] = useState(true);

  const load = useCallback(() => {
    api<VisitRow[]>("/api/v1/visits")
      .then((visits) => setState({ kind: "ok", visits }))
      .catch((e: unknown) =>
        setState({ kind: "error", message: e instanceof Error ? e.message : "Could not load visits." }),
      );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const shown =
    state.kind === "ok"
      ? state.visits.filter((v) => !onlyOpen || ["requested", "rescheduled", "confirmed"].includes(v.state))
      : [];
  const waiting = state.kind === "ok" ? state.visits.filter((v) => v.state === "requested").length : 0;

  return (
    <>
      <h1>Site visits</h1>
      <p className="muted small">
        The agent requests visits; your team confirms them. Call the customer, then confirm or reschedule here.
      </p>
      {waiting > 0 && (
        <div className="notice notice-warn" role="status">
          {waiting} visit request{waiting > 1 ? "s" : ""} waiting for confirmation.
        </div>
      )}
      <div className="actions">
        <label className="inline">
          <input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> Upcoming only
        </label>
      </div>
      {state.kind === "loading" && <Loading />}
      {state.kind === "error" && <ErrorBox message={state.message} onRetry={load} />}
      {state.kind === "ok" && shown.length === 0 && (
        <section className="card">
          <Empty>No {onlyOpen ? "upcoming " : ""}visits.</Empty>
        </section>
      )}
      {shown.map((v) => (
        <section key={v.id} className="card" aria-label={`Visit ${v.customer_name ?? ""}`}>
          <div className="project-head">
            <h2>
              {formatDate(v.date)} at {v.slot} · {v.project}
            </h2>
            <Badge value={v.state} />
          </div>
          <p className="muted small">
            {v.visitor_name ?? v.customer_name ?? "Unknown"} · {v.phone ?? "no phone"} · requested via {v.requested_via}
            {v.notes && <> · {v.notes}</>}
            {v.lead_id && (
              <>
                {" "}
                · <Link href={`/leads/${v.lead_id}`}>open lead</Link>
              </>
            )}
          </p>
          <VisitActions visit={v} onDone={load} />
        </section>
      ))}
    </>
  );
}
