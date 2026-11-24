"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ErrorBox, Loading, Notice } from "@/components/Status";
import { api } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Summary = {
  today: { new_leads: number; calls: number; visits_requested: number; visits_confirmed: number };
  waiting_confirmation: number;
  last_30_days: { qualified_leads: number; visits_booked: number; visit_booking_rate: number | null };
  usage: {
    since: string;
    calls: number;
    call_minutes: number;
    call_minutes_limit: number;
    ai_requests: number;
    ai_cost_usd: number;
    ai_cost_limit_usd: number;
    whatsapp_messages: number;
    warnings: string[];
  };
  paused: boolean;
  paused_reason: string | null;
};
type State = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; data: Summary };

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="stat">
      <span className="muted small">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PauseControl({ data, onDone }: { data: Summary; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function set(paused: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/controls/pause", { method: "POST", body: JSON.stringify({ paused, reason }) });
      setReason("");
      onDone();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "That did not work. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      {data.paused ? (
        <button type="button" disabled={busy} onClick={() => void set(false)}>
          Resume the agent
        </button>
      ) : (
        <div className="actions">
          <input
            aria-label="Reason (optional)"
            placeholder="Reason (optional)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={200}
          />
          <button type="button" className="danger" disabled={busy} onClick={() => void set(true)}>
            Pause the agent
          </button>
        </div>
      )}
      {error && (
        <p className="error small" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export default function HomePage() {
  const me = useMe();
  const [state, setState] = useState<State>({ kind: "loading" });
  const load = useCallback(() => {
    api<Summary>("/api/v1/summary")
      .then((data) => setState({ kind: "ok", data }))
      .catch((e: unknown) =>
        setState({ kind: "error", message: e instanceof Error ? e.message : "Could not load the summary." }),
      );
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  if (state.kind === "loading") return <Loading />;
  if (state.kind === "error") return <ErrorBox message={state.message} onRetry={load} />;
  const { data } = state;
  const rate = data.last_30_days.visit_booking_rate;
  const u = data.usage;

  return (
    <>
      <h1>Today</h1>
      {data.paused && (
        <Notice kind="error">
          <strong>The agent is paused.</strong> Every call goes straight to your team&apos;s number and chats get the
          &quot;team will contact you&quot; message.{data.paused_reason && ` Reason: ${data.paused_reason}.`}
        </Notice>
      )}
      {data.waiting_confirmation > 0 && (
        <Notice kind="warn">
          {data.waiting_confirmation} site visit{data.waiting_confirmation > 1 ? "s" : ""} waiting for your
          confirmation. <Link href="/visits">Open visits</Link>
        </Notice>
      )}
      {u.warnings.map((w) => (
        <Notice key={w} kind="warn">
          {w}.
        </Notice>
      ))}

      <section className="card">
        <div className="cards">
          <Stat label="New leads" value={data.today.new_leads} />
          <Stat label="Calls answered" value={data.today.calls} />
          <Stat label="Visits requested" value={data.today.visits_requested} />
          <Stat label="Visits confirmed" value={data.today.visits_confirmed} />
        </div>
      </section>

      <section className="card">
        <h2>Visit-booking rate</h2>
        <p className="muted small">
          Last 30 days: of the buyers who were qualified, how many agreed to a site visit.
        </p>
        <div className="cards">
          <Stat label="Booking rate" value={rate === null ? "—" : `${Math.round(rate * 100)}%`} />
          <Stat label="Qualified buyers" value={data.last_30_days.qualified_leads} />
          <Stat label="Visits booked" value={data.last_30_days.visits_booked} />
        </div>
      </section>

      <section className="card">
        <h2>Usage this month</h2>
        <p className="muted small">Since {u.since}. Costs are estimates; your providers&apos; bills are final.</p>
        <div className="cards">
          <Stat label="Calls" value={u.calls} />
          <Stat label="Call minutes" value={`${u.call_minutes} / ${u.call_minutes_limit}`} />
          <Stat label="AI requests" value={u.ai_requests} />
          <Stat label="AI cost (USD)" value={`${u.ai_cost_usd.toFixed(2)} / ${u.ai_cost_limit_usd}`} />
          <Stat label="WhatsApp messages" value={u.whatsapp_messages} />
        </div>
      </section>

      <section className="card">
        <h2>Agent</h2>
        <p className="muted small">
          Pause the agent for a holiday, an emergency or anything unexpected. Calls then go straight to your team.
        </p>
        {me?.role === "client_admin" ? (
          <PauseControl data={data} onDone={load} />
        ) : (
          <p className="small">
            {data.paused ? "Paused." : "Running."} Only an admin can pause or resume the agent.
          </p>
        )}
      </section>
    </>
  );
}
