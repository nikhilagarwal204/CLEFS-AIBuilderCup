"use client";

/* Exchanges a magic-link token for a session with a POST from the page,
   never on the GET: WhatsApp's link-preview fetcher requests the URL before
   the owner taps it, and a GET exchange would burn one of the token's three
   uses (or all of them) before a human ever arrives. Crawlers and preview
   bots do not run this script, so only a real browser spends a use. */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../../lib/api";
import { loginHref, safeNextPath } from "../../../lib/session";
import type { MagicExchangeResponse } from "../../../lib/types";
import { Button, Card, Skeleton } from "../../../ui";
import AuthFrame from "../../../login/AuthFrame";

type State = { kind: "working" } | { kind: "error"; title: string; body: string; retry: boolean };

function describe(err: unknown): State {
  if (err instanceof ApiError) {
    if (err.status === 0) {
      return { kind: "error", title: "Can't reach Clefs", body: "Check your connection, then try again. The link is still valid.", retry: true };
    }
    if (err.status === 429) {
      return { kind: "error", title: "Too many attempts", body: "Wait a minute, then try again.", retry: true };
    }
    if (err.status >= 500) {
      return { kind: "error", title: "Something went wrong", body: "Clefs had a problem signing you in. Try again in a moment.", retry: true };
    }
  }
  return {
    kind: "error",
    title: "This link has expired",
    body: "Sign-in links work for 24 hours and a few taps. Sign in with your password, or ask for a fresh link from tomorrow's brief.",
    retry: false,
  };
}

export default function MagicExchange({ token }: { token: string }) {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "working" });
  // React StrictMode runs effects twice in development; each run would spend
  // one of the token's uses. The ref survives the double invoke.
  const started = useRef(false);

  const exchange = useCallback(async () => {
    setState({ kind: "working" });
    try {
      const res = await api.post<MagicExchangeResponse>("/auth/magic/exchange", { token }, { silent401: true });
      const fallback = res.hotel_slug ? `/app/${encodeURIComponent(res.hotel_slug)}` : "/login";
      // replace, not push: the token URL must not stay in history.
      router.replace(safeNextPath(res.next_path, fallback));
    } catch (err) {
      setState(describe(err));
    }
  }, [token, router]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void exchange();
  }, [exchange]);

  if (state.kind === "working") {
    return (
      <AuthFrame>
        <Card>
          <h1 className="cx-auth__title">Signing you in</h1>
          <p className="cx-auth__sub">One moment while we check your link.</p>
          <div style={{ marginTop: 16 }} aria-busy="true"><Skeleton lines={2} /></div>
        </Card>
      </AuthFrame>
    );
  }

  return (
    <AuthFrame footer={<Link className="cx-link" href="/">Clefs home</Link>}>
      <Card>
        <h1 className="cx-auth__title">{state.title}</h1>
        <p className="cx-auth__sub">{state.body}</p>
        <div className="cx-stack" style={{ gap: 8, marginTop: 18 }}>
          {state.retry && <Button variant="primary" block onClick={() => void exchange()}>Try again</Button>}
          <Button variant={state.retry ? "secondary" : "primary"} block href={loginHref()}>Sign in with password</Button>
        </div>
      </Card>
    </AuthFrame>
  );
}
