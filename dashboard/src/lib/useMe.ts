"use client";

import { useEffect, useState } from "react";
import { api, type Me } from "./api";

// Signed-in user, or null while loading. Redirects to /login when the session is gone.
export function useMe(): Me | null {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    let alive = true;
    api<Me>("/api/v1/auth/me")
      .then((user) => {
        if (alive) setMe(user);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return me;
}
