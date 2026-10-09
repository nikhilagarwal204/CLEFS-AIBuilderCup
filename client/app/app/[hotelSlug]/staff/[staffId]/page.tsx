"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { API_PREFIX, ApiError, apiFetch, hotelPath, openHotelStream } from "../../../../lib/api";
import { DEFAULT_TIMEZONE, localDateKey } from "../../../../lib/format";

// Same-origin proxy (next.config rewrite): the device cookie rides along.
const API_BASE = API_PREFIX;

/* One worker screen (05 spec, staff-proof audit): the floor-device app at
   /device/{slug} is the deck's worker screen, with the job in the worker's
   language, the photo check and replies to the guest. This page bypasses
   all of that (legacy PATCH, no ack, no proof), so a hotel that has left
   legacy open access is sent there. Hotels still on legacy open access keep
   the page (CONTRACTS.md: redirecting them before every hotel has paired
   devices would break working personal links), with a link to the app.
   A network blip never redirects; only a definite answer does. */
type Mode = "checking" | "legacy" | "moving";

/* Phase 1 of the personal-link retirement (03 spec 1.8): the date comes from
   the deploy, because the server has no sunset setting yet. Unset means the
   banner still shows, without a date, so staff start asking for a device. */
const LINK_SUNSET = process.env.NEXT_PUBLIC_STAFF_LINK_SUNSET || "";

/* The alert identity of a task. Keyed on reopen count and assignee as well as
   the id, so a reopened job or one reassigned to this person chimes again
   (an id-only set never forgets, so those used to arrive silently). */
function alertKey(t: { id: number; reopen_count?: number | null; assigned_to?: string | null }): string {
  return `${t.id}:${t.reopen_count ?? 0}:${t.assigned_to ?? ""}`;
}

/* ── Types ── */
interface StaffMember {
  id: number;
  name: string;
  role: string;
  department: string;
  is_active: boolean;
  is_online: boolean;
}

interface Task {
  id: number;
  room: string;
  type: string;
  subtype?: string;
  description: string;
  timestamp: number;
  status: "new" | "progress" | "completed";
  category: string;
  priority: "high" | "medium" | "low";
  assigned_to?: string;
  accepted_at?: number;
  resolved_at?: number;
  // v2 fields; absent on a server that predates Service Control v2
  state?: string;
  reopen_count?: number;
}

/* ── Helpers ── */
function relativeTime(ts: number): string {
  const diff = Math.floor((Date.now() - ts) / 1000);
  if (diff < 60) return "Just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

/* Jobs this person finished today, "today" being the hotel's calendar day
   (a Dubai night shift on a phone set to another zone used to miscount). */
function countDoneToday(all: Task[], staffName: string, tz: string): number {
  const today = localDateKey(Date.now(), tz);
  return all.filter(t =>
    t.assigned_to === staffName &&
    t.status === "completed" &&
    !!t.resolved_at &&
    localDateKey(t.resolved_at, tz) === today
  ).length;
}

const DEPT_COLORS: Record<string, string> = {
  "Housekeeping":    "#a78bfa",
  "Food & Beverage": "#fb923c",
  "Maintenance":     "#60a5fa",
  "Front Desk":      "#34d399",
  "Concierge":       "#E1AF68",
  "General":         "#94a3b8",
};

const PRIORITY_STYLES: Record<string, { bg: string; color: string; label: string }> = {
  high:   { bg: "rgba(220,38,38,0.15)",  color: "#ef4444", label: "URGENT" },
  medium: { bg: "rgba(245,158,11,0.15)", color: "#F59E0B", label: "NORMAL" },
  low:    { bg: "rgba(99,102,241,0.15)", color: "#818cf8", label: "LOW"    },
};

function playChime() {
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    [880, 1100, 1320].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, ctx.currentTime + i * 0.12);
      gain.gain.linearRampToValueAtTime(0.4, ctx.currentTime + i * 0.12 + 0.04);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.12 + 0.4);
      osc.start(ctx.currentTime + i * 0.12);
      osc.stop(ctx.currentTime + i * 0.12 + 0.4);
    });
  } catch (_) {}
}

/* Wall clock for event handlers (kept out of the component body for the purity lint). */
function nowMs(): number {
  return Date.now();
}

/* "31 Oct 2026" from a YYYY-MM-DD deploy setting; a calendar day, so UTC. */
function formatSunset(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return day;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

/* ── Main component ── */
export default function StaffPersonalView() {
  const routeParams = useParams();
  const router = useRouter();
  const hotelSlug = routeParams?.hotelSlug as string;
  const staffId = routeParams?.staffId as string;
  const deviceHref = `/device/${encodeURIComponent(hotelSlug ?? "")}`;

  const [mode, setMode] = useState<Mode>("checking");
  const [staff, setStaff] = useState<StaffMember | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [resolving, setResolving] = useState<number | null>(null);
  const [isOnline, setIsOnline] = useState(false);
  const [statusLoading, setStatusLoading] = useState(false);
  // Alert bookkeeping (Phase 0 hotfix, 03 spec 1.8). hydratedRef is set once
  // after the first load: the old "set size > list length" test was true
  // forever after the first resolve (the set only grows), so no later
  // assignment ever chimed.
  const hydratedRef = useRef(false);
  const alertKeysRef = useRef<Set<string>>(new Set());
  const notifPermRef = useRef(false);
  const [hotelTz, setHotelTz] = useState(DEFAULT_TIMEZONE);
  const [completedToday, setCompletedToday] = useState(0);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  };

  /* ── Request browser notification permission once ── */
  useEffect(() => {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().then(p => { notifPermRef.current = p === "granted"; });
    } else if ("Notification" in window && Notification.permission === "granted") {
      notifPermRef.current = true;
    }
  }, []);

  /* ── Which worker screen this hotel uses, and its timezone ("done today"
        is the hotel's day, not the phone's) ── */
  useEffect(() => {
    let live = true;
    apiFetch<{ hotel?: { timezone?: string }; legacy_open_access?: boolean }>(hotelPath(hotelSlug, "/context"), { silent401: true })
      .then(c => {
        if (!live) return;
        if (c?.hotel?.timezone) setHotelTz(c.hotel.timezone);
        setMode(c?.legacy_open_access ? "legacy" : "moving");
      })
      .catch(err => {
        if (!live) return;
        // 401/403/404: this link cannot work here any more; offline: stay put.
        setMode(err instanceof ApiError && err.status !== 0 && err.status < 500 ? "moving" : "legacy");
      });
    return () => { live = false; };
  }, [hotelSlug]);

  useEffect(() => {
    if (mode === "moving") router.replace(deviceHref);
  }, [mode, router, deviceHref]);

  /* ── Fetch tasks assigned to this staff member (one list feeds both counts) ── */
  const fetchMyTasks = useCallback(async (staffName: string) => {
    try {
      const res = await fetch(`${API_BASE}/hotels/${hotelSlug}/requests`);
      if (!res.ok) return;
      const all: Task[] = await res.json();
      const mine = all.filter(t => t.assigned_to === staffName && t.status !== "completed");

      const keys = new Set(mine.map(alertKey));
      const fresh = mine.filter(t => !alertKeysRef.current.has(alertKey(t)));
      // Forget jobs that left this person's list (done, reassigned away), so
      // one that comes back (reopened, handed back) alerts again. The legacy
      // list carries no reopen_count, so this is what catches a reopen.
      alertKeysRef.current = keys;
      // The first load only learns what is already on the list.
      if (hydratedRef.current) {
        fresh.forEach(task => {
          playChime();
          showToast(`New task assigned. Room ${task.room}: ${task.type}`);
          if (notifPermRef.current) {
            new Notification(`New task: Room ${task.room}`, {
              body: `${task.type}${task.subtype ? ` · ${task.subtype}` : ""}: ${task.description}`,
              icon: "/brand/notification-icon.png",
              tag: `task-${alertKey(task)}`,
            });
          }
        });
      }
      hydratedRef.current = true;
      setTasks(mine);
      setCompletedToday(countDoneToday(all, staffName, hotelTz));
    } catch (_) {}
  }, [hotelSlug, hotelTz]);

  /* ── Initial load (legacy hotels only) ── */
  useEffect(() => {
    if (mode !== "legacy") return;
    async function init() {
      try {
        const res = await fetch(`${API_BASE}/hotels/${hotelSlug}/staff/${staffId}`);
        if (!res.ok) { setNotFound(true); setLoading(false); return; }
        const s: StaffMember = await res.json();
        setStaff(s);
        setIsOnline(s.is_online);
      } catch (_) {
        setNotFound(true);
      } finally {
        setLoading(false);
      }
    }
    init();
  }, [hotelSlug, staffId, mode]);

  /* ── Realtime: stream with a polling fallback (openHotelStream) ── */
  useEffect(() => {
    if (!staff) return;
    const refetch = () => { void fetchMyTasks(staff.name); };
    refetch();
    return openHotelStream(hotelSlug, { requests_changed: refetch }, { refetch });
  }, [staff, hotelSlug, fetchMyTasks]);

  /* ── Resolve a task ── */
  const resolveTask = async (taskId: number) => {
    setResolving(taskId);
    try {
      const res = await fetch(`${API_BASE}/hotels/${hotelSlug}/requests/${taskId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "completed", resolved_at: nowMs() }),
      });
      if (!res.ok) {
        showToast("Could not mark it done. Try again.");
        return;
      }
      setTasks(prev => prev.filter(t => t.id !== taskId));
      setCompletedToday(n => n + 1);
      showToast("Task marked as done!");
    } catch (_) {
      showToast("No connection. Try again.");
    } finally {
      setResolving(null);
    }
  };

  /* ── Toggle Duty Status ── */
  const toggleDuty = async () => {
    if (!staff) return;
    setStatusLoading(true);
    try {
      const newStatus = !isOnline;
      const res = await fetch(`${API_BASE}/hotels/${hotelSlug}/staff/${staffId}/status?is_online=${newStatus}`, {
        method: "PATCH"
      });
      if (res.ok) {
        setIsOnline(newStatus);
        setStaff(prev => prev ? { ...prev, is_online: newStatus } : null);
        showToast(newStatus ? "You are now ON DUTY" : "You are now OFF DUTY");
      }
    } catch (_) {
      showToast("Failed to update status");
    } finally {
      setStatusLoading(false);
    }
  };

  /* ── Loading / moving / not found states ── */
  if (mode !== "legacy" || loading) {
    return (
      <div style={{ minHeight: "100vh", background: "#070C14", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 14, padding: 32 }}>
        <div style={{ color: "rgba(255,255,255,0.3)", fontSize: "1rem" }}>{mode === "moving" ? "Opening the staff app…" : "Loading…"}</div>
        {mode === "moving" && (
          <Link href={deviceHref} replace style={{ color: "#E1AF68", fontSize: "0.9rem" }}>Open the staff app</Link>
        )}
      </div>
    );
  }

  if (notFound || !staff) {
    return (
      <div style={{ minHeight: "100vh", background: "#070C14", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 16, padding: 32 }}>
        <div style={{ fontSize: "3rem" }}>🤷</div>
        <h2 style={{ color: "#fff", fontFamily: "sans-serif", fontWeight: 700, fontSize: "1.4rem" }}>Staff member not found</h2>
        <p style={{ color: "rgba(255,255,255,0.4)", fontFamily: "sans-serif", textAlign: "center" }}>This link may be invalid or the staff member was removed.</p>
      </div>
    );
  }

  const deptColor = DEPT_COLORS[staff.department] || "#94a3b8";
  const hotelName = hotelSlug.split("-").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");

  return (
    <div style={{
      minHeight: "100vh",
      background: "#070C14",
      color: "#e2e8f0",
      fontFamily: "'Inter', -apple-system, sans-serif",
      maxWidth: "480px",
      margin: "0 auto",
      padding: "0 0 80px",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #070C14; }

        @keyframes slideUp {
          from { opacity: 0; transform: translateY(16px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes pulseRing {
          0%   { box-shadow: 0 0 0 0 rgba(225, 175, 104,0.5); }
          70%  { box-shadow: 0 0 0 12px rgba(225, 175, 104,0); }
          100% { box-shadow: 0 0 0 0 rgba(225, 175, 104,0); }
        }

        .task-card {
          background: rgba(255,255,255,0.04);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 20px;
          padding: 20px;
          animation: slideUp 0.3s ease forwards;
          transition: border-color 0.2s;
        }
        .task-card:active { transform: scale(0.99); }

        .resolve-btn {
          width: 100%;
          padding: 16px;
          border-radius: 14px;
          border: none;
          background: #16A34A;
          color: #fff;
          font-size: 1rem;
          font-weight: 700;
          font-family: 'Inter', sans-serif;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          margin-top: 16px;
          transition: all 0.15s;
          letter-spacing: 0.01em;
        }
        .resolve-btn:active { transform: scale(0.97); background: #15803d; }
        .resolve-btn:disabled { background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.3); cursor: default; }

        .toast {
          position: fixed;
          bottom: 24px;
          left: 50%;
          transform: translateX(-50%);
          background: #16A34A;
          color: #fff;
          padding: 14px 24px;
          border-radius: 100px;
          font-weight: 600;
          font-size: 0.9rem;
          white-space: nowrap;
          z-index: 9999;
          animation: slideUp 0.3s ease;
          box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        }

        .stat-chip {
          background: rgba(255,255,255,0.05);
          border: 1px solid rgba(255,255,255,0.1);
          border-radius: 12px;
          padding: 12px 18px;
          text-align: center;
          flex: 1;
        }
      `}</style>

      {/* Header */}
      <div style={{
        background: "rgba(255,255,255,0.03)",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
        padding: "20px 24px 18px",
        position: "sticky",
        top: 0,
        backdropFilter: "blur(12px)",
        zIndex: 10,
      }}>
        <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.3)", marginBottom: "8px", textTransform: "uppercase", letterSpacing: "0.08em" }}>
          {hotelName}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <div style={{
            width: "44px", height: "44px", borderRadius: "50%",
            background: `${deptColor}25`, border: `2px solid ${deptColor}55`,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: "1.2rem", fontWeight: 700, color: deptColor, flexShrink: 0,
          }}>
            {staff.name.charAt(0)}
          </div>
          <div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "#fff" }}>Hi, {staff.name.split(" ")[0]} 👋</div>
            <div style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", marginTop: "2px" }}>
              {staff.role} ·{" "}
              <span style={{ color: deptColor }}>{staff.department}</span>
            </div>
          </div>
          {/* Duty Toggle */}
          <div style={{ marginLeft: "auto", display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "6px" }}>
            <button 
              onClick={toggleDuty}
              disabled={statusLoading}
              style={{
                background: isOnline ? "rgba(239,68,68,0.1)" : "rgba(22,163,74,0.1)",
                border: `1px solid ${isOnline ? "rgba(239,68,68,0.3)" : "rgba(22,163,74,0.3)"}`,
                color: isOnline ? "#ef4444" : "#16A34A",
                padding: "6px 12px",
                borderRadius: "100px",
                fontSize: "0.75rem",
                fontWeight: 700,
                cursor: "pointer",
                transition: "all 0.2s"
              }}
            >
              {statusLoading ? "..." : (isOnline ? "Go Off Duty" : "Go On Duty")}
            </button>
            <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "0.72rem", color: isOnline ? "#16A34A" : "rgba(255,255,255,0.25)" }}>
              <div style={{ 
                width: "7px", height: "7px", borderRadius: "50%", 
                background: isOnline ? "#16A34A" : "rgba(255,255,255,0.2)", 
                animation: isOnline ? "pulseRing 2s infinite" : "none" 
              }} />
              {isOnline ? "Active" : "Offline"}
            </div>
          </div>
        </div>
      </div>

      {/* Retirement notice (Phase 1): personal links give way to paired floor devices */}
      <div role="status" style={{
        margin: "16px 20px 0", padding: "12px 14px", borderRadius: "12px",
        background: "rgba(225,175,104,0.10)", border: "1px solid rgba(225,175,104,0.35)",
        color: "#E1AF68", fontSize: "0.8rem", lineHeight: 1.45,
      }}>
        {LINK_SUNSET
          ? <>This link stops working on {formatSunset(LINK_SUNSET)}. Ask your manager to pair a floor device.</>
          : <>This personal link is being retired. Ask your manager to pair a floor device.</>}
        {" "}
        <Link href={deviceHref} style={{ color: "#F2C67D", fontWeight: 600 }}>Open the staff app</Link>
      </div>

      {/* Stats row */}
      <div style={{ padding: "20px 20px 0", display: "flex", gap: "10px" }}>
        <div className="stat-chip">
          <div style={{ fontSize: "1.8rem", fontWeight: 700, color: tasks.length > 0 ? "#E1AF68" : "#fff" }}>{tasks.length}</div>
          <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.4)", marginTop: "4px" }}>Active tasks</div>
        </div>
        <div className="stat-chip">
          <div style={{ fontSize: "1.8rem", fontWeight: 700, color: "#16A34A" }}>{completedToday}</div>
          <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.4)", marginTop: "4px" }}>Done today</div>
        </div>
      </div>

      {/* Task list */}
      <div style={{ padding: "20px" }}>
        {tasks.length === 0 ? (
          <div style={{
            background: "rgba(255,255,255,0.03)",
            border: "1px solid rgba(255,255,255,0.06)",
            borderRadius: "20px",
            padding: "52px 24px",
            textAlign: "center",
            marginTop: "8px",
          }}>
            <div style={{ fontSize: "2.8rem", marginBottom: "14px" }}>✅</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 600, color: "#fff", marginBottom: "8px" }}>All clear!</div>
            <div style={{ fontSize: "0.88rem", color: "rgba(255,255,255,0.35)" }}>No pending tasks right now. New ones will appear here automatically.</div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            {/* Sort: high priority first, then by timestamp (oldest first) */}
            {[...tasks]
              .sort((a, b) => {
                const pOrder = { high: 0, medium: 1, low: 2 };
                const pd = pOrder[a.priority] - pOrder[b.priority];
                return pd !== 0 ? pd : a.timestamp - b.timestamp;
              })
              .map(task => {
                const ps = PRIORITY_STYLES[task.priority] || PRIORITY_STYLES.medium;
                const isResolving = resolving === task.id;
                return (
                  <div key={task.id} className="task-card">
                    {/* Top row */}
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "10px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                        <span style={{ fontWeight: 700, fontSize: "1.05rem", color: "#E1AF68" }}>Room {task.room}</span>
                        <span style={{ color: "rgba(255,255,255,0.25)" }}>·</span>
                        <span style={{ fontSize: "0.9rem", fontWeight: 600, color: "#fff" }}>{task.type}</span>
                        {task.subtype && (
                          <span style={{ fontSize: "0.8rem", color: "rgba(255,255,255,0.4)", fontStyle: "italic" }}>· {task.subtype}</span>
                        )}
                      </div>
                      <span style={{
                        padding: "3px 10px", borderRadius: "100px", fontSize: "0.68rem",
                        fontWeight: 700, letterSpacing: "0.05em",
                        background: ps.bg, color: ps.color,
                        border: `1px solid ${ps.color}44`,
                        flexShrink: 0,
                      }}>
                        {ps.label}
                      </span>
                    </div>

                    {/* Description */}
                    <p style={{ fontSize: "0.95rem", color: "rgba(255,255,255,0.75)", lineHeight: 1.5, marginBottom: "12px" }}>
                      {task.description}
                    </p>

                    {/* Meta row */}
                    <div style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "0.75rem", color: "rgba(255,255,255,0.3)" }}>
                      <span>⏱ {relativeTime(task.timestamp)}</span>
                      <span style={{
                        padding: "2px 8px", borderRadius: "6px",
                        background: `${DEPT_COLORS[task.category] || "#94a3b8"}18`,
                        color: DEPT_COLORS[task.category] || "#94a3b8",
                      }}>
                        {task.category}
                      </span>
                    </div>

                    {/* Resolve button */}
                    <button
                      className="resolve-btn"
                      onClick={() => resolveTask(task.id)}
                      disabled={isResolving}
                    >
                      {isResolving ? (
                        <>⏳ Marking done…</>
                      ) : (
                        <>✓ Mark as Done</>
                      )}
                    </button>
                  </div>
                );
              })}
          </div>
        )}
      </div>

      {/* Footer */}
      <div style={{ textAlign: "center", padding: "16px", fontSize: "0.72rem", color: "rgba(255,255,255,0.15)" }}>
        Powered by Clefs · Live updates
      </div>

      {/* Toast */}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
