"use client";

import { useCallback, useEffect, useState } from "react";

type Health = {
  status: string;
  checks: Record<string, string>;
  client: string;
  languages: string[];
  voice_ready: boolean;
  template_version: string;
};

type HealthState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; health: Health };

export default function HealthPage() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const res = await fetch("/api/v1/health", { cache: "no-store" });
      const body = (await res.json()) as Health;
      setState({ kind: "loaded", health: body });
    } catch {
      setState({ kind: "error", message: "The backend is not reachable." });
    }
  }, []);

  useEffect(() => {
    // Initial fetch on mount; load() only sets state after the request settles.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  return (
    <section className="card narrow">
      <h1>System status</h1>
      {state.kind === "loading" && <p className="muted">Checking…</p>}
      {state.kind === "error" && (
        <p role="alert" className="error">
          {state.message}
        </p>
      )}
      {state.kind === "loaded" && (
        <>
          <p className={state.health.status === "ok" ? "ok" : "error"} data-testid="overall">
            {state.health.status === "ok" ? "All systems OK" : "Degraded"}
          </p>
          <table className="kv">
            <tbody>
              {Object.entries(state.health.checks).map(([name, value]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{value}</td>
                </tr>
              ))}
              <tr>
                <td>Client</td>
                <td>{state.health.client}</td>
              </tr>
              <tr>
                <td>Languages</td>
                <td>{state.health.languages.join(", ")}</td>
              </tr>
              <tr>
                <td>Voice configured</td>
                <td>{state.health.voice_ready ? "yes" : "not yet (calls go to a human)"}</td>
              </tr>
              <tr>
                <td>Template version</td>
                <td>{state.health.template_version}</td>
              </tr>
            </tbody>
          </table>
        </>
      )}
      <button type="button" style={{ marginTop: 16 }} onClick={() => void load()}>
        Refresh
      </button>
    </section>
  );
}
