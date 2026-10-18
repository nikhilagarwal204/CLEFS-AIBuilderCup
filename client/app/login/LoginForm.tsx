"use client";

/* Email + password sign-in. The API sets the HttpOnly clefs_session cookie
   on this origin through the /api/clefs rewrite; the client never sees or
   stores a token. Errors never say whether an email exists. */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ApiError, api } from "../lib/api";
import { safeNextPath } from "../lib/session";
import type { LoginResponse, Membership } from "../lib/types";
import { ROLE_LABELS } from "../lib/types";
import { Button, Card, Field, Input } from "../ui";
import AuthFrame from "./AuthFrame";

function messageFor(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Please try again.";
  if (err.status === 0) return err.message;
  if (err.status === 401 || err.code === "invalid_credentials") return "That email and password don't match. Check both and try again.";
  if (err.status === 429) return "Too many attempts. Wait a minute, then try again.";
  if (err.status === 403) return err.message || "This account can't sign in right now. Ask your hotel owner.";
  if (err.status >= 500) return "Clefs is having a problem right now. Please try again in a moment.";
  return err.message;
}

export default function LoginForm({ next }: { next?: string }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});
  const [hotels, setHotels] = useState<Membership[] | null>(null);
  const [noHotel, setNoHotel] = useState<"admin" | "none" | null>(null);
  const [linkSent, setLinkSent] = useState(false);
  const [linkBusy, setLinkBusy] = useState(false);

  const target = safeNextPath(next, "");

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const fe: typeof fieldErrors = {};
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) fe.email = "Enter the email you were invited with.";
    if (!password) fe.password = "Enter your password.";
    setFieldErrors(fe);
    if (fe.email || fe.password) return;

    setBusy(true);
    setError(null);
    try {
      const res = await api.post<LoginResponse>("/auth/login", { email: email.trim().toLowerCase(), password }, { silent401: true });
      if (target) {
        router.replace(target);
        return;
      }
      const ms = res.memberships ?? [];
      if (ms.length === 1) {
        router.replace(`/app/${encodeURIComponent(ms[0].hotel_slug)}`);
        return;
      }
      if (ms.length > 1) setHotels(ms);
      else setNoHotel(res.user?.is_clefs_admin ? "admin" : "none");
      setBusy(false);
    } catch (err) {
      setError(messageFor(err));
      setPassword("");
      setBusy(false);
    }
  };

  const sendLink = async () => {
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) {
      setFieldErrors({ email: "Enter your email first, then ask for a link." });
      return;
    }
    setLinkBusy(true);
    try {
      await api.post("/auth/magic/request", { email: email.trim().toLowerCase(), purpose: "login" }, { silent401: true });
    } catch (err) {
      // Same answer whether or not the account exists; only a dead network is worth saying.
      if (err instanceof ApiError && err.status === 0) {
        setError(err.message);
        setLinkBusy(false);
        return;
      }
    }
    setLinkSent(true);
    setLinkBusy(false);
  };

  if (hotels) {
    return (
      <AuthFrame>
        <Card>
          <h1 className="cx-auth__title">Choose a hotel</h1>
          <p className="cx-auth__sub">Your account has access to more than one property.</p>
          <div className="cx-stack" style={{ gap: 8, marginTop: 16 }}>
            {hotels.map(h => (
              <Button key={h.hotel_slug} block variant="secondary" href={`/app/${encodeURIComponent(h.hotel_slug)}`}
                      style={{ justifyContent: "space-between" }}>
                <span>{h.hotel_name}</span>
                <span className="cx-faint" style={{ fontWeight: 400 }}>{ROLE_LABELS[h.role] ?? h.role}</span>
              </Button>
            ))}
          </div>
        </Card>
      </AuthFrame>
    );
  }

  if (noHotel) {
    return (
      <AuthFrame>
        <Card>
          <h1 className="cx-auth__title">You&apos;re signed in</h1>
          <p className="cx-auth__sub">
            {noHotel === "admin"
              ? "You're a Clefs admin with no hotel membership. Open a hotel's console link, e.g. /app/<hotel-slug>."
              : "Your account isn't linked to a hotel yet. Ask the hotel owner or GM to add you under Settings, Team."}
          </p>
        </Card>
      </AuthFrame>
    );
  }

  return (
    <AuthFrame footer={<>Guest? You don&apos;t need an account: scan the code in your room. <Link className="cx-link" href="/">Clefs home</Link></>}>
      <Card>
        <h1 className="cx-auth__title">Sign in to your hotel</h1>
        <p className="cx-auth__sub">For owners, managers and hotel staff.</p>
        <form onSubmit={onSubmit} noValidate className="cx-stack" style={{ gap: 14, marginTop: 18 }}>
          {error && <div className="cx-auth__alert" role="alert">{error}</div>}
          {linkSent && (
            <div className="cx-auth__note" role="status">
              If that email has an account, a sign-in link is on its way. It works for 24 hours.
            </div>
          )}
          <Field label="Email" error={fieldErrors.email}>
            <Input type="email" autoComplete="username" inputMode="email" autoFocus value={email}
                   invalid={!!fieldErrors.email} onChange={e => setEmail(e.target.value)} />
          </Field>
          <Field label="Password" error={fieldErrors.password}>
            <Input type="password" autoComplete="current-password" value={password}
                   invalid={!!fieldErrors.password} onChange={e => setPassword(e.target.value)} />
          </Field>
          <Button type="submit" variant="primary" block loading={busy}>Sign in</Button>
          <Button variant="ghost" block loading={linkBusy} disabled={busy || linkSent} onClick={sendLink}>
            Email me a sign-in link instead
          </Button>
        </form>
      </Card>
    </AuthFrame>
  );
}
