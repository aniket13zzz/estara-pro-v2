"use client";

import { useState } from "react";
import { api, formatDate, type VisitRow } from "@/lib/api";

type Slot = { slot_id: string; date: string; time: string };

// Staff controls for one site visit. Only staff ever confirm a visit; the agent only requests.
export function VisitActions({ visit, onDone }: { visit: VisitRow; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [slots, setSlots] = useState<Slot[] | null>(null);
  const [slot, setSlot] = useState("");

  async function act(action: string, slotId?: string) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/visits/${visit.id}/action`, {
        method: "POST",
        body: JSON.stringify({ action, slot_id: slotId ?? null }),
      });
      setSlots(null);
      onDone();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "That did not work. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function openReschedule() {
    setError(null);
    try {
      const open = await api<Slot[]>(`/api/v1/visits/${visit.id}/slots`);
      setSlots(open);
      setSlot(open[0]?.slot_id ?? "");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not load open slots.");
    }
  }

  const holding = ["requested", "rescheduled", "confirmed"].includes(visit.state);
  return (
    <div className="stack">
      <div className="actions">
        {(visit.state === "requested" || visit.state === "rescheduled") && (
          <button type="button" disabled={busy} onClick={() => void act("confirm")}>
            Confirm
          </button>
        )}
        {holding && (
          <button type="button" className="secondary" disabled={busy} onClick={() => void openReschedule()}>
            Reschedule
          </button>
        )}
        {visit.state === "confirmed" && (
          <>
            <button type="button" className="secondary" disabled={busy} onClick={() => void act("complete")}>
              Visited
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={() => void act("no_show")}>
              No-show
            </button>
          </>
        )}
        {holding && (
          <button type="button" className="danger" disabled={busy} onClick={() => void act("cancel")}>
            Cancel
          </button>
        )}
      </div>
      {slots && slots.length === 0 && <p className="muted small">No open slots in the next weeks.</p>}
      {slots && slots.length > 0 && (
        <div className="actions">
          <select value={slot} onChange={(e) => setSlot(e.target.value)} aria-label="New time">
            {slots.map((s) => (
              <option key={s.slot_id} value={s.slot_id}>
                {formatDate(s.date)} at {s.time}
              </option>
            ))}
          </select>
          <button type="button" disabled={busy || !slot} onClick={() => void act("reschedule", slot)}>
            Save new time
          </button>
          <button type="button" className="link-button" onClick={() => setSlots(null)}>
            Close
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
