/* ═══════════════════════════════════════════════════════════════════════
   Device alerts (03 spec 1.6): a repeating Web Audio chime until the job is
   acknowledged, vibration on Android, and the Screen Wake Lock so a floor
   device does not sleep through its queue.

   - Browsers only allow audio after a user gesture, so the AudioContext is
     created and resumed inside the PIN-pad submit (unlock()). Until then the
     UI shows "Tap to turn on sound".
   - The loop is level-triggered, not edge-triggered: the app calls
     setAlarm() with what the queue looks like now ("chime" while anything
     for this person is unacked, "urgent" for L1 / Security loops), and the
     loop stops by itself when the answer becomes "off". Nothing can get
     "stuck ringing" after the ack, and nothing new arrives silently.
   - Tones are synthesised (no audio files), short, and pitched for a noisy
     corridor: a two-note chime, or a fast two-tone siren for urgent.
   ═══════════════════════════════════════════════════════════════════════ */

import type { Card } from "./deviceApi";

export type AlarmMode = "off" | "chime" | "urgent";

// ─── What should ring (pure, tested in outbox.test.mjs) ──────────────────

const UNACKED = new Set(["new", "assigned", "reopened"]);

export interface AlarmScope {
  /** Everyone on an open PIN shift on this device. */
  staffIds: number[];
  deviceId: number | null;
  /** Requests an `escalated` feed event asked this device to loop for. */
  loopIds: Set<number>;
}

/**
 * "urgent" while an L1 / emergency / sensitive request in view is unacked,
 * or an escalation asked this device to loop (Security, 03 spec 1.4);
 * "chime" while a job assigned to someone on this device (or to the device
 * itself, the shared-device pool) is unacked; else "off". `cards` must
 * already be filtered to the device's scope.
 */
export function alarmFor(cards: Card[], scope: AlarmScope): AlarmMode {
  let chime = false;
  for (const c of cards) {
    if (!UNACKED.has(c.state)) continue;
    const urgent = c.severity === "L1" || c.sensitivity === "emergency" || c.sensitivity === "sensitive";
    if (urgent || scope.loopIds.has(c.id)) return "urgent";
    const mine = (c.assigned_staff_id != null && scope.staffIds.includes(c.assigned_staff_id))
      || (scope.deviceId != null && c.assigned_device_id === scope.deviceId);
    if (mine && c.state === "assigned") chime = true;
  }
  return chime ? "chime" : "off";
}

const CHIME_EVERY_MS = 6_000;
const URGENT_EVERY_MS = 1_600;

type WakeLockSentinelLike = { release(): Promise<void>; released?: boolean };

export class DeviceAlerts {
  private ctx: AudioContext | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private mode: AlarmMode = "off";
  private wakeLock: WakeLockSentinelLike | null = null;
  private wantWake = false;
  private muteUntil = 0;
  private onVisible = () => {
    if (document.visibilityState === "visible" && this.wantWake) void this.acquireWake();
  };

  /** True once audio may play (after a gesture). */
  get unlocked(): boolean {
    return Boolean(this.ctx && this.ctx.state === "running");
  }

  /** Call from a click / key handler (the PIN submit). */
  unlock(): void {
    try {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return;
      if (!this.ctx) this.ctx = new Ctor();
      void this.ctx.resume();
      // A silent blip primes iOS Safari, which otherwise stays suspended.
      this.tone(0, 0.01, 440, 0.0001);
    } catch { /* no audio on this device: vibration and the screen still work */ }
  }

  /** Quiet the loop for a moment (the person is reading the sheet). */
  snooze(ms: number): void {
    this.muteUntil = Date.now() + ms;
  }

  setAlarm(mode: AlarmMode): void {
    if (mode === this.mode) return;
    this.mode = mode;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (mode === "off") { this.vibrate(0); return; }
    this.muteUntil = 0;
    const every = mode === "urgent" ? URGENT_EVERY_MS : CHIME_EVERY_MS;
    this.cycle();
    this.timer = setInterval(() => this.cycle(), every);
  }

  /** One chime, for news that needs no ack (a note, a handover). */
  ping(): void {
    this.chime(0.6);
    this.vibrate([80]);
  }

  // ─── Wake lock ───

  async keepAwake(on: boolean): Promise<void> {
    this.wantWake = on;
    if (on) {
      document.addEventListener("visibilitychange", this.onVisible);
      await this.acquireWake();
    } else {
      document.removeEventListener("visibilitychange", this.onVisible);
      try { await this.wakeLock?.release(); } catch { /* already released */ }
      this.wakeLock = null;
    }
  }

  private async acquireWake(): Promise<void> {
    const nav = navigator as Navigator & { wakeLock?: { request(type: "screen"): Promise<WakeLockSentinelLike> } };
    if (!nav.wakeLock || (this.wakeLock && !this.wakeLock.released)) return;
    try { this.wakeLock = await nav.wakeLock.request("screen"); } catch { /* battery saver or not allowed */ }
  }

  dispose(): void {
    this.setAlarm("off");
    void this.keepAwake(false);
    try { void this.ctx?.close(); } catch { /* ignore */ }
    this.ctx = null;
  }

  // ─── Sound ───

  private cycle(): void {
    if (Date.now() < this.muteUntil) return;
    if (this.mode === "urgent") {
      this.siren();
      this.vibrate([300, 120, 300, 120, 300]);
    } else if (this.mode === "chime") {
      this.chime(1);
      this.vibrate([200, 100, 200]);
    }
  }

  private chime(gain: number): void {
    this.tone(0, 0.35, 880, 0.25 * gain);
    this.tone(0.18, 0.5, 1318.5, 0.2 * gain);
  }

  private siren(): void {
    for (let i = 0; i < 3; i++) {
      this.tone(i * 0.36, 0.18, 1046.5, 0.3);
      this.tone(i * 0.36 + 0.18, 0.18, 784, 0.3);
    }
  }

  private tone(at: number, dur: number, freq: number, gain: number): void {
    const ctx = this.ctx;
    if (!ctx || ctx.state !== "running") return;
    const t = ctx.currentTime + at;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, t);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(Math.max(gain, 0.0002), t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(g).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + dur + 0.05);
  }

  private vibrate(pattern: number | number[]): void {
    try { navigator.vibrate?.(pattern); } catch { /* not on iOS */ }
  }
}
