"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { Badge, Empty, ErrorBox, Loading } from "@/components/Status";
import { VisitActions } from "@/components/VisitActions";
import { api, formatDate, formatDateTime, formatDuration, type LeadDetail } from "@/lib/api";

type State = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; lead: LeadDetail };

const TIMELINE: Record<string, string> = {
  "1": "Within a month",
  "0": "Ready to move",
};

function timeline(months: number | null | undefined): string {
  if (months === null || months === undefined) return "—";
  return TIMELINE[String(months)] ?? `Within ${months} months`;
}

function yesNo(value: boolean | null | undefined): string {
  return value === null || value === undefined ? "—" : value ? "Yes" : "No";
}

export default function LeadPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(() => {
    api<LeadDetail>(`/api/v1/leads/${id}`)
      .then((lead) => setState({ kind: "ok", lead }))
      .catch((e: unknown) =>
        setState({ kind: "error", message: e instanceof Error ? e.message : "Could not load this lead." }),
      );
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (state.kind === "loading") return <Loading />;
  if (state.kind === "error") return <ErrorBox message={state.message} onRetry={load} />;
  const { lead } = state;
  const r = lead.requirements;
  const nextAction = lead.handoffs.find((h) => h.next_action)?.next_action;

  return (
    <>
      <p className="small">
        <Link href="/leads">← All leads</Link>
      </p>
      <div className="project-head">
        <h1>{lead.customer.name ?? "Unknown caller"}</h1>
        <Badge value={lead.state} />
      </div>
      <p className="muted small">
        {lead.customer.phone ?? "No phone number"} · via {lead.source.replace("_", " ")}
        {lead.customer.preferred_language && ` · speaks ${lead.customer.preferred_language === "hi" ? "Hindi" : "English"}`}
      </p>
      {nextAction && (
        <div className="notice notice-warn" role="status">
          <strong>Next action:</strong> {nextAction}
        </div>
      )}

      <section className="card">
        <h2>What they need</h2>
        <table className="kv" aria-label="Requirements">
          <tbody>
            <tr><td>Budget</td><td>{r.budget ?? "—"}</td></tr>
            <tr><td>Area</td><td>{r.locations?.length ? r.locations.join(", ") : "—"}</td></tr>
            <tr><td>Home</td><td>{[r.bhk?.length ? `${r.bhk.join("/")} BHK` : null, r.property_type].filter(Boolean).join(" ") || "—"}</td></tr>
            <tr><td>Move in</td><td>{timeline(r.timeline_months)}</td></tr>
            <tr><td>Home loan</td><td>{yesNo(r.loan_needed)}</td></tr>
            <tr><td>Purpose</td><td>{r.purpose === "self_use" ? "To live in" : r.purpose === "investment" ? "Investment" : "—"}</td></tr>
            <tr>
              <td>Score</td>
              <td>
                {lead.score ?? "—"} {lead.band && <Badge value={lead.band} />}
                {lead.score_reason && <div className="muted small">{lead.score_reason}</div>}
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Site visits</h2>
        {lead.visits.length === 0 ? (
          <Empty>No visit requested yet.</Empty>
        ) : (
          lead.visits.map((v) => (
            <div key={v.id} className="stack" style={{ marginBottom: 16 }}>
              <div>
                <strong>{v.project}</strong> · {formatDate(v.date)} at {v.slot} <Badge value={v.state} />
                {v.visitor_name && <span className="muted small"> · visitor {v.visitor_name}</span>}
              </div>
              <VisitActions visit={v} onDone={load} />
            </div>
          ))
        )}
      </section>

      <section className="card">
        <h2>Matched properties</h2>
        {lead.matches.length === 0 ? (
          <Empty>No matches yet.</Empty>
        ) : (
          <ol>
            {lead.matches.map((m) => (
              <li key={m.project_code}>
                <strong>{m.project}</strong>
                {m.reason && <div className="muted small">{m.reason}</div>}
              </li>
            ))}
          </ol>
        )}
        {lead.objections.length > 0 && (
          <>
            <h3>Concerns raised</h3>
            <ul>
              {lead.objections.map((o) => (
                <li key={o.at}>
                  <strong>{o.type.replaceAll("_", " ")}</strong>: <span lang={o.language}>{o.text}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="card">
        <h2>Calls</h2>
        {lead.calls.length === 0 ? (
          <Empty>No calls.</Empty>
        ) : (
          lead.calls.map((c) => (
            <div key={c.id} className="stack" style={{ marginBottom: 12 }}>
              <div>
                <Link href={`/calls/${c.id}`}>{formatDateTime(c.started_at)}</Link> · {formatDuration(c.duration_seconds)}
                {c.outcome && <> · <Badge value={c.outcome} /></>}
              </div>
              {c.summary && <p className="small">{c.summary}</p>}
            </div>
          ))
        )}
      </section>

      <section className="card">
        <h2>Conversation</h2>
        {lead.timeline.length === 0 ? (
          <Empty>No messages.</Empty>
        ) : (
          <ul className="timeline">
            {lead.timeline.map((m, i) => (
              <li key={`${m.at}-${i}`} className={m.from === "customer" ? "from-customer" : "from-agent"}>
                <span className="muted small">
                  {m.from === "customer" ? "Customer" : m.from === "agent" ? "Agent" : m.from} · {m.channel} ·{" "}
                  {formatDateTime(m.at)}
                </span>
                <div lang={m.language}>{m.text}</div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
