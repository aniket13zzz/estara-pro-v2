"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ErrorBox, Loading, Notice } from "@/components/Status";
import { api } from "@/lib/api";

type Settings = { ready: boolean; missing: string[]; public_key: string | null; assistant_id: string | null };
type Line = { role: string; text: string };
type VapiClient = {
  start: (assistantId: string) => Promise<unknown>;
  stop: () => void;
  on: (event: string, handler: (arg: unknown) => void) => void;
};

// A browser call to the agent, using Vapi's web SDK with the public key (made for browsers).
// The call goes through the same assistant and Sales Brain as a real phone call.
export default function TestCallPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<"idle" | "connecting" | "live" | "ended">("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const vapi = useRef<VapiClient | null>(null);

  useEffect(() => {
    api<Settings>("/api/v1/voice/test-call")
      .then(setSettings)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load call settings."));
    return () => vapi.current?.stop();
  }, []);

  async function start() {
    if (!settings?.public_key || !settings.assistant_id) return;
    setError(null);
    setLines([]);
    setStatus("connecting");
    try {
      const { default: Vapi } = await import("@vapi-ai/web");
      const client = new Vapi(settings.public_key) as unknown as VapiClient;
      vapi.current = client;
      client.on("call-start", () => setStatus("live"));
      client.on("call-end", () => setStatus("ended"));
      client.on("error", () => {
        setError("The call could not continue. Check the Vapi settings in docs/demo.md.");
        setStatus("ended");
      });
      client.on("message", (m: unknown) => {
        const msg = m as { type?: string; transcriptType?: string; role?: string; transcript?: string };
        if (msg.type === "transcript" && msg.transcriptType === "final" && msg.transcript) {
          setLines((prev) => [...prev, { role: msg.role ?? "?", text: msg.transcript ?? "" }]);
        }
      });
      await client.start(settings.assistant_id);
    } catch {
      setError("Could not start the call. Allow the microphone and check your internet connection.");
      setStatus("idle");
    }
  }

  function stop() {
    vapi.current?.stop();
    setStatus("ended");
  }

  if (error && !settings) return <ErrorBox message={error} />;
  if (!settings) return <Loading />;

  return (
    <>
      <h1>Test call</h1>
      <p className="muted small">
        Talk to the agent from this browser, in Hindi or English, exactly as a caller would. Use your microphone.
      </p>
      {!settings.ready && (
        <Notice kind="warn">
          <strong>Voice is not set up yet.</strong> Missing: {settings.missing.join("; ")}. Follow docs/demo.md.
        </Notice>
      )}
      {settings.ready && (
        <section className="card">
          <div className="actions">
            {status !== "live" && status !== "connecting" && (
              <button type="button" onClick={() => void start()}>
                {status === "ended" ? "Call again" : "Start call"}
              </button>
            )}
            {(status === "live" || status === "connecting") && (
              <button type="button" className="danger" onClick={stop}>
                End call
              </button>
            )}
            <span className="muted small" role="status">
              {status === "connecting" && "Connecting…"}
              {status === "live" && "On the call"}
              {status === "ended" && (
                <>
                  Call ended. The summary appears on the <Link href="/calls">Calls</Link> page in a minute.
                </>
              )}
            </span>
          </div>
          {error && <ErrorBox message={error} />}
          {lines.length > 0 && (
            <ul className="timeline" aria-label="Live transcript">
              {lines.map((l, i) => (
                <li key={i} className={l.role === "user" ? "from-customer" : "from-agent"}>
                  <span className="muted small">{l.role === "user" ? "You" : "Agent"}</span>
                  <div>{l.text}</div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  );
}
