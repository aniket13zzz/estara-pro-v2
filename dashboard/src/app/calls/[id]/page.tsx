"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { Badge, ErrorBox, Loading, Notice } from "@/components/Status";
import { api, formatDateTime, formatDuration, type CallRow } from "@/lib/api";

type State = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; call: CallRow };

export default function CallPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [state, setState] = useState<State>({ kind: "loading" });
  const load = useCallback(() => {
    api<CallRow>(`/api/v1/calls/${id}`)
      .then((call) => setState({ kind: "ok", call }))
      .catch((e: unknown) =>
        setState({ kind: "error", message: e instanceof Error ? e.message : "Could not load this call." }),
      );
  }, [id]);
  useEffect(() => {
    load();
  }, [load]);

  if (state.kind === "loading") return <Loading />;
  if (state.kind === "error") return <ErrorBox message={state.message} onRetry={load} />;
  const c = state.call;
  return (
    <>
      <p className="small">
        <Link href="/calls">← All calls</Link>
      </p>
      <div className="project-head">
        <h1>Call · {formatDateTime(c.started_at)}</h1>
        {c.outcome && <Badge value={c.outcome} />}
      </div>
      <p className="muted small">
        {c.caller_phone ?? "Test call"} · {formatDuration(c.duration_seconds)}
        {c.ended_reason && ` · ended: ${c.ended_reason}`}
        {c.cost_usd !== null && ` · cost USD ${c.cost_usd.toFixed(2)}`}
        {c.lead_id && (
          <>
            {" "}
            · <Link href={`/leads/${c.lead_id}`}>open lead</Link>
          </>
        )}
      </p>
      {c.unverified_numbers.length > 0 && (
        <Notice kind="error">
          <strong>Check this call.</strong> The agent said {c.unverified_numbers.join(", ")}, which no approved
          data or caller words contained. Listen to the recording and, if a price or date was wrong, call the
          customer back with the correct one.
        </Notice>
      )}
      <section className="card">
        <h2>Summary</h2>
        <p>{c.summary ?? <span className="muted">No summary yet (it appears when the call ends).</span>}</p>
        {c.recording_url && (
          <>
            <h3>Recording</h3>
            <audio controls src={c.recording_url} preload="none">
              <a href={c.recording_url}>Download the recording</a>
            </audio>
          </>
        )}
      </section>
      <section className="card">
        <h2>Transcript</h2>
        {c.transcript ? <pre className="transcript">{c.transcript}</pre> : <p className="muted">No transcript yet.</p>}
      </section>
    </>
  );
}
