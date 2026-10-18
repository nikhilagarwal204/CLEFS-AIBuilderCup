"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Console shell: one 56px top bar (brand, hotel, module tabs, user menu)
   and, on phones, a bottom tab bar. There is deliberately NO left rail:
   StaffDashboard, mounted unchanged as the Service module, keeps its own
   sidebar as the Service sub-nav, and a second rail would eat a portrait
   tablet. The CSS below slides that sidebar (and its mobile bar) down to
   sit under our top bar.

   The shell also makes the access decision the proxy cannot: /auth/me says
   who this is, /hotels/{slug}/context says what the hotel has enabled, and
   an entry shows only when both allow it. Legacy open-access hotels with
   no session see just Service, exactly like the old dashboard.
   ═══════════════════════════════════════════════════════════════════════ */

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { LuLogIn, LuLogOut, LuMoreHorizontal, LuPlus } from "react-icons/lu";
import { useHotel } from "../../../lib/hotel";
import { loginHref, useSession } from "../../../lib/session";
import { ROLE_LABELS, type Role } from "../../../lib/types";
import { Button, Card, EmptyState, Modal, Skeleton, UiStyles } from "../../../ui";
import { activeNavKey, NAV, navHref, type NavItem } from "./nav";
import { NEW_REQUEST_SHORTCUT, newRequestShortcutLabel, shouldOpenNewRequest } from "./newRequestShortcut";

const shellStyles = `
.cx-shell {
  --cx-top: 56px;
  --cx-bottom: 0px;
  min-height: 100vh; min-height: 100dvh;
  background-color: var(--clefs-slate-900);
  color: var(--clefs-ink);
  font-family: var(--clefs-sans);
}
@media (max-width: 767.98px) {
  .cx-shell[data-bottombar="true"] { --cx-bottom: calc(60px + env(safe-area-inset-bottom)); }
}

.cx-topbar {
  position: sticky; top: 0; z-index: 998; height: var(--cx-top);
  display: flex; align-items: center; gap: 10px; padding: 0 12px 0 14px;
  background: rgba(3, 6, 12, 0.88);
  -webkit-backdrop-filter: blur(16px); backdrop-filter: blur(16px);
  border-bottom: 1px solid var(--clefs-line-soft);
}
.cx-brand { display: flex; align-items: center; gap: 10px; min-width: 0; text-decoration: none; flex-shrink: 1; }
.cx-brand img { flex-shrink: 0; }
.cx-brand__hotel {
  font-family: var(--clefs-serif); font-size: 1.02rem; font-weight: 500; color: var(--clefs-gold-500);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 52vw;
}
@media (min-width: 768px) { .cx-brand__hotel { max-width: 220px; } }
.cx-brand__sample {
  flex-shrink: 0; padding: 2px 8px; border: 1px solid var(--clefs-line-soft); border-radius: 999px;
  font-size: 0.68rem; letter-spacing: 0.04em; color: var(--clefs-gold-500); white-space: nowrap;
}
.cx-modtabs { display: none; }
@media (min-width: 768px) {
  .cx-modtabs {
    display: flex; flex: 1; min-width: 0; height: 100%; align-items: stretch; gap: 2px;
    overflow-x: auto; scrollbar-width: none; margin-left: 10px;
  }
  .cx-modtabs::-webkit-scrollbar { display: none; }
  /* Fifteen modules do not fit every laptop: fade the trailing edge so a
     scrollable strip reads as scrollable (the scrollbar itself is hidden). */
  .cx-modtabs { -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 28px), transparent);
                mask-image: linear-gradient(to right, #000 calc(100% - 28px), transparent); padding-right: 24px; }
}
.cx-modtab {
  display: inline-flex; align-items: center; gap: 7px; padding: 0 11px; margin-bottom: -1px;
  color: var(--clefs-ink-muted); text-decoration: none; font-size: 0.84rem; font-weight: 500; white-space: nowrap;
  border-bottom: 2px solid transparent; transition: color 0.15s;
}
.cx-modtab svg { font-size: 0.95rem; opacity: 0.85; }
.cx-modtab:hover { color: var(--clefs-ink); }
.cx-modtab[aria-current="page"] { color: var(--clefs-gold-400); border-bottom-color: var(--clefs-gold-500); }
@media (min-width: 768px) and (max-width: 1599.98px) {
  /* Labels carry the meaning; icons return when there is room for both. */
  .cx-modtab svg { display: none; }
  .cx-modtab { padding: 0 9px; }
}

.cx-usermenu { position: relative; margin-left: auto; flex-shrink: 0; }
.cx-avatar {
  width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center; cursor: pointer;
  background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line); color: var(--clefs-gold-400);
  font: 600 0.8rem/1 var(--clefs-sans);
}
.cx-avatar:hover { border-color: var(--clefs-gold-500); }
.cx-usermenu .cx-menu { min-width: 240px; }
.cx-usermenu__who { padding: 8px 10px 10px; border-bottom: 1px solid var(--clefs-line-soft); margin-bottom: 4px; }
.cx-usermenu__name { font-weight: 600; font-size: 0.88rem; }
.cx-usermenu__meta { font-size: 0.76rem; color: var(--clefs-ink-faint); margin-top: 3px; overflow-wrap: anywhere; }
.cx-usermenu__label { font-size: 0.66rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--clefs-ink-faint); padding: 8px 10px 4px; }
.cx-usermenu__kbd {
  margin-left: auto; padding: 2px 6px; border-radius: 6px; border: 1px solid var(--clefs-line-soft);
  font: 500 0.7rem/1.3 var(--clefs-sans); color: var(--clefs-ink-faint); white-space: nowrap;
}

.cx-bottombar {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 998; height: var(--cx-bottom);
  padding-bottom: env(safe-area-inset-bottom); display: flex;
  background: rgba(3, 6, 12, 0.94); -webkit-backdrop-filter: blur(16px); backdrop-filter: blur(16px);
  border-top: 1px solid var(--clefs-line-soft);
}
@media (min-width: 768px) { .cx-bottombar { display: none; } }
.cx-bottomtab {
  flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px;
  font: 500 0.66rem/1 var(--clefs-sans); color: var(--clefs-ink-faint); text-decoration: none;
  background: none; border: 0; cursor: pointer; -webkit-tap-highlight-color: transparent;
}
.cx-bottomtab svg { font-size: 1.25rem; }
.cx-bottomtab[aria-current="page"] { color: var(--clefs-gold-400); }
.cx-morelist { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.cx-morelist a {
  display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 14px 6px; border-radius: 12px;
  text-decoration: none; color: var(--clefs-ink-muted); font-size: 0.78rem; text-align: center;
  background: rgba(255,255,255,0.03); border: 1px solid var(--clefs-line-soft);
}
.cx-morelist a svg { font-size: 1.3rem; color: var(--clefs-gold-500); }
.cx-morelist a[aria-current="page"] { border-color: var(--clefs-line-gold); color: var(--clefs-gold-400); }

.cx-main { max-width: 1280px; margin: 0 auto; padding: 20px 16px calc(var(--cx-bottom) + 32px); }
@media (min-width: 768px) { .cx-main { padding: 32px 28px 56px; } }
.cx-fullbleed { padding-bottom: var(--cx-bottom); }

/* ── StaffDashboard (Service) sits below the bar ── */
.cx-shell .sidebar-aside { top: var(--cx-top); height: calc(100vh - var(--cx-top)); }
.cx-shell .app-container { min-height: calc(100vh - var(--cx-top)); }
.cx-shell .mobile-top-bar { top: var(--cx-top); }
@media (max-width: 1024px) {
  /* Its drawer is position: fixed there; stop it at our bars, not the viewport edges. */
  .cx-shell .sidebar-aside { height: auto; bottom: var(--cx-bottom); }
}
`;

function initials(name: string | null | undefined): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
}

function UserMenu({ hotelSlug, anonHint, canFile }: { hotelSlug: string; anonHint: boolean; canFile: boolean }) {
  const session = useSession();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const user = session.user;
  if (!user) {
    // Legacy open-access hotel with nobody signed in (the proxy's anon hint
    // agrees). Offer sign-in without forcing it.
    if (session.loading) return <Skeleton width={34} height={34} radius={17} />;
    return (
      <div className="cx-usermenu">
        <Button size="sm" variant="ghost" icon={<LuLogIn />} href={loginHref(pathname ?? `/app/${hotelSlug}`)}
                title={anonHint ? "Viewing without an account" : undefined}>
          Sign in
        </Button>
      </div>
    );
  }

  const others = session.me?.memberships.filter(m => m.hotel_slug !== hotelSlug) ?? [];
  const roleLabel = session.role ? ROLE_LABELS[session.role as Role] ?? session.role : null;

  return (
    <div className="cx-usermenu" ref={ref}>
      <button type="button" className="cx-avatar" aria-haspopup="menu" aria-expanded={open}
              aria-label={`Account menu for ${user.name}`} onClick={() => setOpen(o => !o)}>
        {initials(user.name)}
      </button>
      {open && (
        <div className="cx-menu" role="menu">
          <div className="cx-usermenu__who">
            <div className="cx-usermenu__name">{user.name}</div>
            <div className="cx-usermenu__meta">
              {user.email}
              {roleLabel && <> &middot; {roleLabel}</>}
              {session.isAdmin && <> &middot; Clefs admin</>}
            </div>
          </div>
          {canFile && (
            <>
              {/* Names the console shortcut where people look for account
                  options, and gives pointer users the same route. The menu
                  mounts only after a click, so reading navigator here never
                  disagrees with the server render. */}
              <Link href={`/app/${encodeURIComponent(hotelSlug)}/service/new`} role="menuitem"
                    className="cx-menu__item" aria-keyshortcuts={NEW_REQUEST_SHORTCUT} onClick={() => setOpen(false)}>
                <LuPlus aria-hidden="true" /> New request
                <kbd className="cx-usermenu__kbd">{newRequestShortcutLabel(navigator.platform)}</kbd>
              </Link>
              <div className="cx-menu__sep" />
            </>
          )}
          {others.length > 0 && (
            <>
              <div className="cx-usermenu__label">Switch hotel</div>
              {others.map(m => (
                <Link key={m.hotel_slug} href={`/app/${encodeURIComponent(m.hotel_slug)}`} role="menuitem"
                      className="cx-menu__item" onClick={() => setOpen(false)}>
                  {m.hotel_name}
                </Link>
              ))}
              <div className="cx-menu__sep" />
            </>
          )}
          <button type="button" role="menuitem" className="cx-menu__item" onClick={() => { setOpen(false); void session.logout(); }}>
            <LuLogOut aria-hidden="true" /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

function ShellBody({ hotelSlug, visible, activeItem, children }: {
  hotelSlug: string;
  visible: readonly NavItem[];
  activeItem: NavItem | undefined;
  children: ReactNode;
}) {
  const session = useSession();
  const hotel = useHotel();

  const waiting = (session.loading && !session.me) || (hotel.loading && !hotel.context);
  if (session.error) {
    if (session.error.status === 401) {
      return <EmptyState title="Taking you to sign in" body="This hotel's console needs an account." />;
    }
    return (
      <Card>
        <EmptyState title="We couldn't check your access" body={session.error.message}
                    action={<Button variant="primary" onClick={session.reload}>Try again</Button>} />
      </Card>
    );
  }
  if (hotel.error && !hotel.context) {
    const s = hotel.error.status;
    return (
      <Card>
        <EmptyState
          title={s === 404 ? "Hotel not found" : s === 403 ? "No access to this hotel" : "We couldn't load this hotel"}
          body={s === 404 ? `There is no hotel at "${hotelSlug}". Check the link.`
            : s === 403 ? "Your account is not a member of this hotel. Sign in with a different account or ask the owner to invite you."
            : hotel.error.message}
          action={s === 403
            ? <Button variant="primary" href={loginHref(`/app/${hotelSlug}`)}>Switch account</Button>
            : s === 404 ? undefined : <Button variant="primary" onClick={hotel.reload}>Try again</Button>}
        />
      </Card>
    );
  }
  if (waiting) {
    return (
      <div className="cx-stack" aria-busy="true" aria-label="Loading">
        <Skeleton width="40%" height={30} />
        <Skeleton lines={3} />
        <div className="cx-grid cx-grid--3">
          <Skeleton height={110} radius={16} />
          <Skeleton height={110} radius={16} />
          <Skeleton height={110} radius={16} />
        </div>
      </div>
    );
  }
  if (activeItem && !visible.some(v => v.key === activeItem.key)) {
    const moduleOff = !hotel.moduleEnabled(activeItem.module);
    const home = visible[0];
    return (
      <Card>
        <EmptyState
          icon={<activeItem.icon />}
          title={moduleOff ? `${activeItem.label} is not switched on` : `You don't have access to ${activeItem.label}`}
          body={moduleOff
            ? "An owner or GM can enable it under Settings, Modules."
            : "Ask the hotel owner or GM if you need it."}
          action={home ? <Button variant="primary" href={navHref(hotelSlug, home)}>Go to {home.label}</Button> : undefined}
        />
      </Card>
    );
  }
  return <>{children}</>;
}

export default function ConsoleShell({
  hotelSlug, anonHint = false, children,
}: {
  hotelSlug: string;
  /** From the proxy's x-clefs-anon header: no credential cookie was seen. */
  anonHint?: boolean;
  children: ReactNode;
}) {
  const session = useSession();
  const hotel = useHotel();
  const pathname = usePathname();
  const router = useRouter();
  const [moreOpen, setMoreOpen] = useState(false);

  // Spec 1.8: a console-wide shortcut opens the phone-entry form for anyone
  // who may file a request. It is Alt+N (Option+N on a Mac), never a bare
  // letter: WCAG 2.1.4 bars single-character shortcuts that cannot be
  // switched off, and a stray "n" from dictation or a focused row must not
  // navigate away. The rules live in newRequestShortcut.ts (tested there);
  // the user menu names the shortcut so it is discoverable.
  const canFile = !!session.me && (session.can("service.work") || session.can("service.manage"))
    && hotel.moduleEnabled("service");
  useEffect(() => {
    if (!canFile) return;
    const newPath = `/app/${encodeURIComponent(hotelSlug)}/service/new`;
    const onKey = (e: KeyboardEvent) => {
      const open = shouldOpenNewRequest(e, e.target as HTMLElement | null, {
        dialogOpen: !!document.querySelector("[role='dialog'][aria-modal='true'], dialog[open]"),
        onNewRequestPage: !!pathname?.startsWith(newPath),
      });
      if (!open) return;
      e.preventDefault();
      router.push(newPath);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [canFile, hotelSlug, pathname, router]);

  const active = activeNavKey(pathname, hotelSlug);
  const activeItem = NAV.find(n => n.key === active);

  const visible = useMemo(() => {
    if (!session.me) return [];
    const items = NAV.filter(n => session.can(n.perm) && hotel.moduleEnabled(n.module));
    // Anonymous legacy access must still reach the dashboard even if the
    // server resolves no permissions for an anonymous principal.
    if (!items.length && session.legacyMode) return NAV.filter(n => n.key === "service");
    return items;
  }, [session, hotel]);

  const bottomPrimary = useMemo(() => {
    const primary = visible.filter(n => n.mobilePrimary);
    for (const n of visible) if (primary.length < 4 && !primary.includes(n)) primary.push(n);
    return primary.slice(0, 4);
  }, [visible]);
  const needsMore = visible.length > bottomPrimary.length;
  const showBottomBar = visible.length > 1;
  const fullBleed = Boolean(activeItem?.fullBleed);
  const hotelName = hotel.hotel?.name ?? "";

  // Keep the current module's tab in view when the strip overflows.
  const tabsRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = tabsRef.current?.querySelector<HTMLElement>('[aria-current="page"]');
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [active, visible.length]);

  return (
    <div className="cx-shell cx-root" data-bottombar={showBottomBar || undefined}>
      <UiStyles />
      <style dangerouslySetInnerHTML={{ __html: shellStyles }} />

      <header className="cx-topbar">
        <Link href={`/app/${encodeURIComponent(hotelSlug)}`} className="cx-brand" aria-label={`${hotelName || "Clefs"} console home`}>
          <Image src="/brand/clefs-key.webp" alt="" width={14} height={28} priority />
          {hotelName ? <span className="cx-brand__hotel">{hotelName}</span> : <Skeleton width={120} height={16} />}
          {/* A demo hotel is named like a real one and may carry a prospect's name for a meeting, so every
              screen says its figures are samples (never "your hotel loses AED X"). */}
          {hotel.hotel?.status === "demo" && (
            <span className="cx-brand__sample" title="Every figure for this hotel is sample data">Illustrative sample</span>
          )}
        </Link>
        {visible.length > 1 && (
          <nav className="cx-modtabs" aria-label="Modules" ref={tabsRef}>
            {visible.map(n => (
              <Link key={n.key} href={navHref(hotelSlug, n)} className="cx-modtab"
                    aria-current={n.key === active ? "page" : undefined} title={n.blurb}>
                <n.icon aria-hidden="true" />
                {n.label}
              </Link>
            ))}
          </nav>
        )}
        <UserMenu hotelSlug={hotelSlug} anonHint={anonHint} canFile={canFile} />
      </header>

      {fullBleed ? (
        <div className="cx-fullbleed">
          <ShellBody hotelSlug={hotelSlug} visible={visible} activeItem={activeItem}>{children}</ShellBody>
        </div>
      ) : (
        <main className="cx-main">
          <ShellBody hotelSlug={hotelSlug} visible={visible} activeItem={activeItem}>{children}</ShellBody>
        </main>
      )}

      {showBottomBar && (
        <nav className="cx-bottombar" aria-label="Modules">
          {bottomPrimary.map(n => (
            <Link key={n.key} href={navHref(hotelSlug, n)} className="cx-bottomtab" aria-current={n.key === active ? "page" : undefined}>
              <n.icon aria-hidden="true" />
              {n.label}
            </Link>
          ))}
          {needsMore && (
            <button type="button" className="cx-bottomtab" onClick={() => setMoreOpen(true)}
                    aria-current={activeItem && !bottomPrimary.includes(activeItem) ? "page" : undefined}>
              <LuMoreHorizontal aria-hidden="true" />
              More
            </button>
          )}
        </nav>
      )}

      <Modal open={moreOpen} onClose={() => setMoreOpen(false)} title="All modules">
        <div className="cx-morelist">
          {visible.map(n => (
            <Link key={n.key} href={navHref(hotelSlug, n)} aria-current={n.key === active ? "page" : undefined}
                  onClick={() => setMoreOpen(false)}>
              <n.icon aria-hidden="true" />
              {n.label}
            </Link>
          ))}
        </div>
      </Modal>
    </div>
  );
}
