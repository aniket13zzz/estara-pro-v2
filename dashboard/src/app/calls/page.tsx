"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge, Empty, ErrorBox, Loading } from "@/components/Status";
import { api, formatDateTime, formatDuration, type CallRow } from "@/lib/api";

type State = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; calls: CallRow[] };

export default function CallsPage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const load = useCallback(() => {
    api<CallRow[]>("/api/v1/calls")
      .then((calls) => setState({ kind: "ok", calls }))
      .catch((e: unknown) =>
        setState({ kind: "error", message: e instanceof Error ? e.message : "Could not load calls." }),
      );
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <h1>Calls</h1>
      <p className="muted small">Every call the agent answered, with a short summary for your team.</p>
      {state.kind === "loading" && <Loading />}
      {state.kind === "error" && <ErrorBox message={state.message} onRetry={load} />}
      {state.kind === "ok" && state.calls.length === 0 && (
        <section className="card">
          <Empty>No calls yet. Try one from the Test call page.</Empty>
        </section>
      )}
      {state.kind === "ok" && state.calls.length > 0 && (
        <section className="card">
          <div className="table-wrap">
            <table aria-label="Calls">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Caller</th>
                  <th>Length</th>
                  <th>Outcome</th>
                  <th>Summary</th>
                </tr>
              </thead>
              <tbody>
                {state.calls.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <Link href={`/calls/${c.id}`}>{formatDateTime(c.started_at)}</Link>
                    </td>
                    <td>{c.caller_phone ?? "Test call"}</td>
                    <td>{formatDuration(c.duration_seconds)}</td>
                    <td>
                      {c.outcome ? <Badge value={c.outcome} /> : <span className="muted">in progress</span>}
                      {c.unverified_numbers.length > 0 && <span className="error small"> · check numbers</span>}
                    </td>
                    <td className="small">{c.summary ?? "—"}</td>
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
