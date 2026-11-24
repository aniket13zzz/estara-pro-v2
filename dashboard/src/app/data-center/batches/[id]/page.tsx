"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Badge, Empty, ErrorBox, Loading, Notice } from "@/components/Status";
import { api, ApiError, ENTITY_LABELS, formatDateTime, type Batch, type Finding, type Preview } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type State = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; data: Preview };

function Summary({ batch }: { batch: Batch }) {
  const entities = batch.summary.entities ?? {};
  if (batch.entity_type === "document") {
    return (
      <p>
        Document <strong>{batch.summary.title}</strong> ({batch.summary.doc_type}), indexed into{" "}
        {batch.summary.chunks} searchable passages once approved.
      </p>
    );
  }
  return (
    <div className="table-wrap">
      <table aria-label="Summary">
        <thead>
          <tr>
            <th>Data</th>
            <th className="num">Rows</th>
            <th className="num">New</th>
            <th className="num">Changed</th>
            <th className="num">Unchanged</th>
            <th className="num">Errors</th>
            <th className="num">Warnings</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(entities).map(([name, s]) => (
            <tr key={name}>
              <td>{ENTITY_LABELS[name] ?? name}</td>
              <td className="num">{s.rows}</td>
              <td className="num">{s.new}</td>
              <td className="num">{s.changed}</td>
              <td className="num">{s.unchanged}</td>
              <td className={`num ${s.errors ? "error" : ""}`}>{s.errors}</td>
              <td className="num">{s.warnings}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Findings({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) return <Empty>No problems found.</Empty>;
  return (
    <div className="table-wrap">
      <table aria-label="Validation errors">
        <thead>
          <tr>
            <th>Severity</th>
            <th>Data</th>
            <th className="num">Row</th>
            <th>Column</th>
            <th>Problem</th>
          </tr>
        </thead>
        <tbody>
          {findings.map((f, i) => (
            <tr key={i} className={f.severity === "error" ? "row-error" : undefined}>
              <td>
                <Badge value={f.severity} />
              </td>
              <td>{f.entity ? (ENTITY_LABELS[f.entity] ?? f.entity) : "File"}</td>
              <td className="num">{f.row_number ?? "—"}</td>
              <td>{f.column ? <code>{f.column}</code> : "—"}</td>
              <td>{f.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RowsPreview({ data }: { data: Preview }) {
  if (data.rows.length === 0) return <Empty>No rows.</Empty>;
  return (
    <div className="table-wrap">
      <table aria-label="Rows">
        <thead>
          <tr>
            <th>Data</th>
            <th className="num">Row</th>
            <th>Change</th>
            <th>Values</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r) => (
            <tr key={`${r.entity}-${r.line}`} className={r.valid ? undefined : "row-error"}>
              <td>{ENTITY_LABELS[r.entity] ?? r.entity}</td>
              <td className="num">{r.line}</td>
              <td>
                <Badge value={r.change} />
                {r.changed_fields.length > 0 && <div className="muted small">{r.changed_fields.join(", ")}</div>}
              </td>
              <td className="small">
                {Object.entries(r.values)
                  .filter(([, v]) => v !== null && v !== "")
                  .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
                  .join(" · ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.total_rows > data.rows.length && (
        <p className="muted small">
          Showing the first {data.rows.length} of {data.total_rows} rows.
        </p>
      )}
    </div>
  );
}

export default function BatchPage() {
  const { id } = useParams<{ id: string }>();
  const me = useMe();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [reason, setReason] = useState("");

  const load = useCallback(() => {
    api<Preview>(`/api/v1/datacenter/batches/${id}`)
      .then((data) => setState({ kind: "ok", data }))
      .catch((e: unknown) =>
        setState({ kind: "error", message: e instanceof Error ? e.message : "Could not load this upload." }),
      );
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(action: "approve" | "reject" | "archive") {
    setBusy(true);
    setMessage(null);
    try {
      const body = action === "reject" ? JSON.stringify({ reason }) : undefined;
      const batch = await api<Batch>(`/api/v1/datacenter/batches/${id}/${action}`, { method: "POST", body });
      const text = {
        approve: "Approved. The agent now uses this data.",
        reject: "Rejected. Nothing from this upload will be used.",
        archive: "Archived. These facts are no longer used.",
      }[action];
      setMessage({ kind: "ok", text: `${text} (status: ${batch.status})` });
      load();
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof ApiError ? e.message : "The action failed." });
    } finally {
      setBusy(false);
    }
  }

  if (state.kind === "loading") return <Loading />;
  if (state.kind === "error") return <ErrorBox message={state.message} onRetry={load} />;

  const { batch } = state.data;
  const isAdmin = me?.role === "client_admin";
  const pending = batch.status === "validated" || batch.status === "failed";

  return (
    <>
      <p className="small">
        <Link href="/data-center">← Data Center</Link>
      </p>
      <h1>
        {batch.filename ?? "Upload"} <Badge value={batch.status} />
      </h1>
      <p className="muted small">
        {ENTITY_LABELS[batch.entity_type] ?? batch.entity_type} · uploaded {formatDateTime(batch.created_at)}
        {batch.approved_at && ` · approved ${formatDateTime(batch.approved_at)}`}
        {batch.rejected_reason && ` · rejected: ${batch.rejected_reason}`}
      </p>

      {message && <Notice kind={message.kind}>{message.text}</Notice>}
      {batch.status === "failed" && (
        <Notice kind="error">
          This file has {batch.error_count} error(s). Fix them in your file and upload it again; nothing was
          imported.
        </Notice>
      )}

      <section className="card">
        <h2>Summary</h2>
        <Summary batch={batch} />
        {pending && (
          <div className="actions">
            {isAdmin ? (
              <>
                <button type="button" disabled={!state.data.can_approve || busy} onClick={() => void act("approve")}>
                  Approve
                </button>
                <input
                  aria-label="Reason for rejecting"
                  placeholder="Reason (optional)"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  style={{ maxWidth: 280 }}
                />
                <button type="button" className="secondary" disabled={busy} onClick={() => void act("reject")}>
                  Reject
                </button>
              </>
            ) : (
              <span className="muted small">A client admin must approve this upload.</span>
            )}
          </div>
        )}
        {batch.status === "approved" && isAdmin && (
          <div className="actions">
            <button type="button" className="danger" disabled={busy} onClick={() => void act("archive")}>
              Archive (stop using these facts)
            </button>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Errors and warnings</h2>
        <Findings findings={state.data.findings} />
      </section>

      {batch.entity_type !== "document" && (
        <section className="card">
          <h2>Preview</h2>
          <RowsPreview data={state.data} />
        </section>
      )}
    </>
  );
}
