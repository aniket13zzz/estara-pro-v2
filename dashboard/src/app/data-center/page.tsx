"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Badge, Empty, ErrorBox, Loading, Notice } from "@/components/Status";
import {
  api,
  ApiError,
  ENTITY_LABELS,
  formatDateTime,
  type Alert,
  type Batch,
  type Freshness,
} from "@/lib/api";

const DATA_TYPES = ["projects", "unit_types", "units", "price_lists", "offers", "payment_plans", "amenities"];
const DOC_TYPES = ["brochure", "faq", "legal", "other"];

type Load<T> = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; data: T };

function useLoad<T>(path: string): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ kind: "loading" });
  const reload = useCallback(() => {
    api<T>(path)
      .then((data) => setState({ kind: "ok", data }))
      .catch((e: unknown) =>
        setState({ kind: "error", message: e instanceof Error ? e.message : "Something went wrong." }),
      );
  }, [path]);
  useEffect(() => {
    reload();
  }, [reload]);
  return [state, reload];
}

function isDocument(name: string): boolean {
  return /\.(pdf|docx)$/i.test(name);
}

function UploadForm({ onUploaded }: { onUploaded: (b: Batch) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [entity, setEntity] = useState("");
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("brochure");
  const [projectCode, setProjectCode] = useState("");
  const [state, setState] = useState<{ kind: "idle" | "busy" } | { kind: "error"; message: string }>({
    kind: "idle",
  });
  const doc = file ? isDocument(file.name) : false;

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!file) return;
    setState({ kind: "busy" });
    const form = new FormData();
    form.append("file", file);
    if (doc) {
      if (title) form.append("title", title);
      form.append("doc_type", docType);
      if (projectCode) form.append("project_code", projectCode);
    } else if (entity) {
      form.append("entity", entity);
    }
    try {
      const batch = await api<Batch>("/api/v1/datacenter/uploads", { method: "POST", body: form });
      setState({ kind: "idle" });
      onUploaded(batch);
    } catch (err) {
      setState({ kind: "error", message: err instanceof ApiError ? err.message : "Upload failed." });
    }
  }

  return (
    <form onSubmit={submit} aria-label="Upload data">
      <label htmlFor="file">File (CSV, Excel .xlsx, PDF or Word .docx)</label>
      <input
        id="file"
        type="file"
        accept=".csv,.xlsx,.pdf,.docx"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
      />
      {file && !doc && (
        <>
          <label htmlFor="entity">Data type</label>
          <select id="entity" value={entity} onChange={(e) => setEntity(e.target.value)}>
            <option value="">Detect from file name / workbook sheets</option>
            {DATA_TYPES.map((t) => (
              <option key={t} value={t}>
                {ENTITY_LABELS[t]}
              </option>
            ))}
          </select>
        </>
      )}
      {file && doc && (
        <div className="form-grid">
          <div>
            <label htmlFor="title">Title</label>
            <input id="title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={file.name} />
          </div>
          <div>
            <label htmlFor="doctype">Document type</label>
            <select id="doctype" value={docType} onChange={(e) => setDocType(e.target.value)}>
              {DOC_TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="project">Project code (optional)</label>
            <input id="project" value={projectCode} onChange={(e) => setProjectCode(e.target.value)} />
          </div>
        </div>
      )}
      {state.kind === "error" && <ErrorBox message={state.message} />}
      <div className="actions">
        <button type="submit" disabled={!file || state.kind === "busy"}>
          {state.kind === "busy" ? "Checking file…" : "Upload and check"}
        </button>
        <span className="muted small">Nothing is used by the agent until an admin approves it.</span>
      </div>
    </form>
  );
}

function Alerts({ alerts }: { alerts: Alert[] }) {
  if (alerts.length === 0) return null;
  return (
    <>
      {alerts.map((a) => (
        <Notice key={a.id} kind="warn">
          <span data-testid="alert">{a.message}</span>
        </Notice>
      ))}
    </>
  );
}

function FreshnessTable({ data }: { data: Freshness }) {
  const total = data.entities.reduce((n, e) => n + e.approved + e.pending, 0);
  if (total === 0) return <Empty>No approved data yet. Upload the templates to get started.</Empty>;
  return (
    <div className="table-wrap">
      <table aria-label="Data freshness">
        <thead>
          <tr>
            <th>Data</th>
            <th className="num">Approved</th>
            <th className="num">Stale</th>
            <th className="num">Awaiting approval</th>
            <th>Next goes stale</th>
          </tr>
        </thead>
        <tbody>
          {data.entities.map((e) => (
            <tr key={e.entity}>
              <td>{e.label}</td>
              <td className="num">{e.approved}</td>
              <td className={`num ${e.stale ? "error" : ""}`}>{e.stale}</td>
              <td className="num">{e.pending}</td>
              <td>{formatDateTime(e.next_stale_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Facts go stale {data.stale_after_days} days after approval, or after their valid-to date.</p>
    </div>
  );
}

function BatchesTable({ batches }: { batches: Batch[] }) {
  if (batches.length === 0) return <Empty>No uploads yet.</Empty>;
  return (
    <div className="table-wrap">
      <table aria-label="Uploads">
        <thead>
          <tr>
            <th>File</th>
            <th>Type</th>
            <th>Status</th>
            <th className="num">Rows</th>
            <th className="num">Errors</th>
            <th className="num">Warnings</th>
            <th>Uploaded</th>
          </tr>
        </thead>
        <tbody>
          {batches.map((b) => (
            <tr key={b.id}>
              <td>
                <Link href={`/data-center/batches/${b.id}`}>{b.filename ?? "upload"}</Link>
              </td>
              <td>{ENTITY_LABELS[b.entity_type] ?? b.entity_type}</td>
              <td>
                <Badge value={b.status} />
              </td>
              <td className="num">{b.row_count}</td>
              <td className={`num ${b.error_count ? "error" : ""}`}>{b.error_count}</td>
              <td className="num">{b.warning_count}</td>
              <td>{formatDateTime(b.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function DataCenterPage() {
  const router = useRouter();
  const [batches, reloadBatches] = useLoad<Batch[]>("/api/v1/datacenter/batches");
  const [fresh, reloadFresh] = useLoad<Freshness>("/api/v1/datacenter/freshness");

  return (
    <>
      <h1>Data Center</h1>
      {fresh.kind === "ok" && <Alerts alerts={fresh.data.alerts} />}

      <section className="card">
        <h2>Upload</h2>
        <UploadForm
          onUploaded={(b) => {
            reloadBatches();
            reloadFresh();
            router.push(`/data-center/batches/${b.id}`);
          }}
        />
        <h3>Templates</h3>
        <p className="small">
          <a href="/api/v1/datacenter/templates/inventory.xlsx">Excel workbook (all sheets + README)</a>
          {" · CSV: "}
          {DATA_TYPES.map((t, i) => (
            <span key={t}>
              {i > 0 && ", "}
              <a href={`/api/v1/datacenter/templates/${t}.csv`}>{t}</a>
            </span>
          ))}
        </p>
      </section>

      <section className="card">
        <h2>Freshness</h2>
        {fresh.kind === "loading" && <Loading />}
        {fresh.kind === "error" && <ErrorBox message={fresh.message} onRetry={reloadFresh} />}
        {fresh.kind === "ok" && <FreshnessTable data={fresh.data} />}
      </section>

      <section className="card">
        <h2>Uploads</h2>
        {batches.kind === "loading" && <Loading />}
        {batches.kind === "error" && <ErrorBox message={batches.message} onRetry={reloadBatches} />}
        {batches.kind === "ok" && <BatchesTable batches={batches.data} />}
      </section>
    </>
  );
}
