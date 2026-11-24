"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

type LoginState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "error"; message: string }
  | { kind: "success"; name: string; role: string };

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<LoginState>({ kind: "idle" });

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState({ kind: "submitting" });
    try {
      const res = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ email, password }),
      });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        const detail =
          typeof body === "object" && body !== null && "detail" in body
            ? String((body as { detail: unknown }).detail)
            : `Sign-in failed (${res.status})`;
        setState({ kind: "error", message: detail });
        return;
      }
      const user = (body as { user: { name: string; role: string } }).user;
      setState({ kind: "success", name: user.name, role: user.role });
      router.push("/home");
    } catch {
      setState({ kind: "error", message: "Could not reach the server. Try again." });
    }
  }

  if (state.kind === "success") {
    return (
      <section className="card narrow">
        <h1>Signed in</h1>
        <p>
          Welcome, {state.name} <span className="muted">({state.role})</span>. Opening the Data
          Center…
        </p>
      </section>
    );
  }

  return (
    <section className="card narrow">
      <h1>Sign in</h1>
      <form onSubmit={onSubmit} noValidate>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {state.kind === "error" && (
          <p role="alert" className="error">
            {state.message}
          </p>
        )}
        <button type="submit" style={{ marginTop: 20 }} disabled={state.kind === "submitting" || !email || !password}>
          {state.kind === "submitting" ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </section>
  );
}
