"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge, Empty, ErrorBox, Loading, Notice } from "@/components/Status";
import { api, formatDateTime, type ProjectRow } from "@/lib/api";

type Data = { stale_after_days: number; projects: ProjectRow[]; synthetic_demo_data?: boolean };
type State = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; data: Data };

function Project({ p }: { p: ProjectRow }) {
  return (
    <section className="card" aria-label={p.name}>
      <div className="project-head">
        <h2>
          {p.name} <span className="muted small">({p.code})</span>
        </h2>
        <Badge value={p.status} />
      </div>
      <p className="muted small">
        {[p.locality, p.city].filter(Boolean).join(", ")}
        {p.possession && ` · possession ${p.possession}`}
        {p.approved_at && ` · last updated ${formatDateTime(p.approved_at)}`}
        {p.version && ` · version ${p.version}`}
      </p>
      {p.status === "pending" && (
        <Notice kind="warn">Waiting for approval. The agent does not use this project yet.</Notice>
      )}
      {p.stale && (
        <Notice kind="warn">
          Project details are stale ({p.stale_reason}). Re-upload and approve to refresh them.
        </Notice>
      )}
      {p.units.stale > 0 && (
        <Notice kind="warn">
          {p.units.stale} unit(s) have stale data; the agent will not quote their prices or availability.
        </Notice>
      )}
      {p.pending_versions > 0 && p.status === "approved" && (
        <p className="muted small">A newer version is waiting for approval in the Data Center.</p>
      )}
      {p.status === "approved" && (
        <>
          <div className="cards">
            <div className="stat">
              <span className="muted small">Units</span>
              <strong>{p.units.total}</strong>
            </div>
            <div className="stat">
              <span className="muted small">Available</span>
              <strong>{p.units.available}</strong>
            </div>
          </div>
          <h3>Unit types</h3>
          {p.unit_types.length === 0 ? (
            <Empty>No approved unit types.</Empty>
          ) : (
            <div className="table-wrap">
              <table aria-label={`${p.code} unit types`}>
                <thead>
                  <tr>
                    <th>Type</th>
                    <th className="num">BHK</th>
                    <th>Carpet area</th>
                    <th>Price</th>
                    <th className="num">Units</th>
                    <th className="num">Available</th>
                    <th>Freshness</th>
                  </tr>
                </thead>
                <tbody>
                  {p.unit_types.map((t) => (
                    <tr key={t.code}>
                      <td>
                        {t.name} <span className="muted small">({t.code})</span>
                      </td>
                      <td className="num">{t.bhk}</td>
                      <td>{t.carpet_area ?? "—"}</td>
                      <td>{t.price ?? "—"}</td>
                      <td className="num">{t.units}</td>
                      <td className="num">{t.available}</td>
                      <td>{t.stale ? <span className="error">stale: {t.stale_reason}</span> : "fresh"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

export default function PropertiesPage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const load = useCallback(() => {
    api<Data>("/api/v1/properties")
      .then((data) => setState({ kind: "ok", data }))
      .catch((e: unknown) =>
        setState({ kind: "error", message: e instanceof Error ? e.message : "Could not load properties." }),
      );
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <h1>Properties</h1>
      <p className="muted small">
        Approved data the agent can talk about. Prices and dates shown here are exactly what it will quote.
      </p>
      {state.kind === "loading" && <Loading />}
      {state.kind === "error" && <ErrorBox message={state.message} onRetry={load} />}
      {state.kind === "ok" && state.data.synthetic_demo_data && (
        <div className="notice notice-warn" role="status">
          <strong>Demo data:</strong> these projects are synthetic sample data (synthetic_demo_data),
          not real listings. Replace them with the developer&apos;s inventory in the{" "}
          <Link href="/data-center">Data Center</Link> before going live.
        </div>
      )}
      {state.kind === "ok" && state.data.projects.length === 0 && (
        <section className="card">
          <Empty>
            No projects yet. Upload your inventory in the <Link href="/data-center">Data Center</Link>.
          </Empty>
        </section>
      )}
      {state.kind === "ok" && state.data.projects.map((p) => <Project key={p.code} p={p} />)}
    </>
  );
}
