"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Me } from "@/lib/api";

const LINKS = [
  { href: "/home", label: "Home" },
  { href: "/leads", label: "Leads" },
  { href: "/visits", label: "Visits" },
  { href: "/calls", label: "Calls" },
  { href: "/test-call", label: "Test call" },
  { href: "/data-center", label: "Data Center" },
  { href: "/properties", label: "Properties" },
  { href: "/health", label: "Status" },
];

export function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    if (pathname === "/login") return;
    // Plain fetch: the nav must not redirect pages that are public (e.g. /health).
    fetch("/api/v1/auth/me", { credentials: "same-origin", cache: "no-store" })
      .then((r) => (r.ok ? (r.json() as Promise<Me>) : null))
      .then(setMe)
      .catch(() => setMe(null));
  }, [pathname]);

  async function signOut() {
    try {
      await api("/api/v1/auth/logout", { method: "POST" });
    } finally {
      setMe(null);
      router.push("/login");
    }
  }

  return (
    <header className="topbar">
      <span className="brand">ESTARA Pro</span>
      <nav aria-label="Main">
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href} aria-current={pathname.startsWith(l.href) ? "page" : undefined}>
            {l.label}
          </Link>
        ))}
        {me ? (
          <>
            <span className="muted small" data-testid="whoami">
              {me.name} · {me.role === "client_admin" ? "Admin" : "Sales"}
            </span>
            <button type="button" className="link-button" onClick={() => void signOut()}>
              Sign out
            </button>
          </>
        ) : (
          <Link href="/login">Sign in</Link>
        )}
      </nav>
    </header>
  );
}
