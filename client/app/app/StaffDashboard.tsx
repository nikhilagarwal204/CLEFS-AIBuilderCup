"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  BsBellFill,
  BsCheckCircleFill,
  BsClockHistory,
  BsPersonCircle,
  BsHouseDoor,
  BsSearch,
  BsFilter,
  BsTrash,
  BsPlusLg,
  BsX,
  BsChevronDown,
  BsExclamationCircle,
  BsArrowClockwise,
} from "react-icons/bs";
import {
  MdOutlineCleaningServices,
  MdOutlineRoomService,
  MdOutlineWaterDrop,
  MdOutlineHotel,
  MdOutlineMiscellaneousServices,
} from "react-icons/md";
import {
  LuCrown,
  LuHeart,
  LuUtensilsCrossed,
  LuCompass,
  LuCalendar,
  LuUsers,
  LuPlus,
  LuPencil,
  LuTrash2,
  LuCheck,
  LuSparkles,
  LuTrendingUp,
  LuClock,
  LuChevronDown,
  LuChevronUp,
  LuX as LuXClose,
  LuMenu,
  LuSiren,
  LuFastForward,
} from "react-icons/lu";
import {
  ChartCard,
  ColumnChart,
  HBarChart,
  ShareMeter,
  chartStyles,
  STATUS_SERIES,
  CHART_NEW,
  CHART_QUIET,
  type ColumnDatum,
  type BarRow,
} from "./charts";
import Link from "next/link";
import { API_PREFIX, ApiError, apiFetch, openHotelStream } from "../lib/api";
import { formatDateTime, formatDate, useFormat } from "../lib/format";
import { useHotelOptional } from "../lib/hotel";
import { useSession } from "../lib/session";
import { ROLE_LABELS, type Role, type StreamStatus } from "../lib/types";
import { Drawer, useToast } from "../ui";
// Service Control v2 pieces (03 spec 1.8: surgical edits now, extraction later).
import {
  createRequest, demoAdvance, effectiveState, fetchQueue, takesLegacyPath, transition, conflictText, errorText, isMissingRoute,
  type TransitionAction, type V2Fields,
} from "./[hotelSlug]/(console)/service/_components/serviceApi";
import {
  EtaChip, LivePill, OriginalToggle, PhotoThumb, ProofAlert, StaffAssign, SvcStyles, TimeTaken, V2Chips, useNow,
} from "./[hotelSlug]/(console)/service/_components/RequestMeta";
import { hotelDayStart, hotelHour } from "./[hotelSlug]/(console)/service/_components/hotelTime";
import PhoneEntryConsole from "./[hotelSlug]/(console)/service/_components/PhoneEntryConsole";
import { V2AnalyticsStrip } from "./[hotelSlug]/(console)/service/_components/V2Analytics";
import { StaffDeviceActions } from "./[hotelSlug]/(console)/service/_components/StaffDeviceActions";
// 05 spec (AI Builder Cup): approvals, request detail and the deck's wording.
import ApprovalsPanel from "./[hotelSlug]/(console)/service/_components/ApprovalsPanel";
import RequestDetail from "./[hotelSlug]/(console)/service/_components/RequestDetail";
import {
  escalationLabel, escalationStep, graceStatus, isEmergency, isOpenRow, isUrgentPath, priorityClass, priorityLabel,
} from "./[hotelSlug]/(console)/service/_components/deskLabels";


/* ─────────────────────────────── Styles ─────────────────────────────── */
const injectStyles = `
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

  @keyframes slideIn {
    from { opacity: 0; transform: translateY(20px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  @keyframes fadeIn {
    from { opacity: 0; }
    to   { opacity: 1; }
  }
  @keyframes scaleIn {
    from { opacity: 0; transform: scale(0.95) translateY(10px); }
    to   { opacity: 1; transform: scale(1) translateY(0); }
  }
  @keyframes pulse {
    0%   { transform: scale(1);    box-shadow: 0 0 0 0   rgba(225, 175, 104, 0.4); }
    70%  { transform: scale(1.05); box-shadow: 0 0 0 10px rgba(225, 175, 104, 0); }
    100% { transform: scale(1);    box-shadow: 0 0 0 0   rgba(225, 175, 104, 0); }
  }
  @keyframes shimmer { to { background-position: -200% center; } }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  /* Display type only. Task text, labels and controls stay in Inter - a serif
     UI is elegant for three seconds and tiring for an eight-hour shift. */
  .dashboard-header h1, .sd-brand__name, .sd-stat__value {
    font-family: var(--clefs-serif);
    font-weight: 500;
    letter-spacing: -0.005em;
  }
  .sd-brand { display: flex; align-items: center; gap: 11px; min-width: 0; }
  .sd-brand__mark { height: 30px; width: auto; flex-shrink: 0; }
  .sd-brand__name {
    font-size: 1.18rem; color: var(--clefs-gold-500); line-height: 1.2;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .sd-brand--sm .sd-brand__mark { height: 24px; }
  .sd-brand--sm .sd-brand__name { font-size: 1rem; }

  /* Stat tiles: a gold hairline on top instead of a heavier border, and the
     figure set in the brand serif. */
  .sd-stat { position: relative; overflow: hidden; }
  .sd-stat::after {
    content: ''; position: absolute; inset: 0 0 auto 0; height: 1px;
    background: linear-gradient(90deg, transparent, var(--clefs-line-gold), transparent);
  }
  .sd-stat__label {
    font-size: 0.72rem; letter-spacing: 0.13em; text-transform: uppercase;
    color: var(--clefs-ink-faint); margin-bottom: 12px;
  }
  .sd-stat__value { font-size: 2.3rem; line-height: 1; }
  .sd-stat__sub { font-size: 0.72rem; color: rgba(242,238,230,0.26); margin-top: 9px; }

  body {
    background: var(--hc-slate);
    color: #e2e8f0;
    font-family: 'Inter', sans-serif;
  }

  /* ── Glassmorphism cards ── */
  .glass-card {
    background: rgba(255,255,255,0.03);
    backdrop-filter: blur(12px);
    border: 1px solid var(--clefs-line-soft);
    border-radius: 14px;
    transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
  }
  .glass-card:hover {
    border-color: rgba(225, 175, 104,0.3);
    background: rgba(255,255,255,0.05);
    transform: translateY(-2px);
  }

  /* ── Status badges ── */
  .status-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    white-space: nowrap;      /* "In Progress" must never wrap inside a pill */
    line-height: 1.25;
    padding: 6px 14px;
    border-radius: 100px;
    font-size: 0.75rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .status-new      { background:rgba(225, 175, 104,0.15); color:var(--hc-gold); border:1px solid rgba(225, 175, 104,0.3); animation:pulse 2s infinite; }
  .status-progress { background:rgba(var(--hc-blue-rgb),0.15);  color:var(--hc-blue); border:1px solid rgba(var(--hc-blue-rgb),0.3); }
  .status-completed{ background:rgba(var(--hc-green-rgb),0.15);   color:var(--hc-green); border:1px solid rgba(var(--hc-green-rgb),0.3); }

  /* ── Priority badges ── */
  .priority-high, .priority-medium, .priority-low {
    display: inline-flex; align-items: center; justify-content: center;
    white-space: nowrap; line-height: 1.25;
  }
  .priority-high   { background:rgba(var(--hc-urgent-rgb),0.15);  color:var(--hc-urgent); border:1px solid rgba(var(--hc-urgent-rgb),0.3);  padding:3px 10px; border-radius:100px; font-size:0.7rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; }
  .priority-medium { background:rgba(var(--hc-warning-rgb),0.15); color:var(--hc-warning); border:1px solid rgba(var(--hc-warning-rgb),0.3); padding:3px 10px; border-radius:100px; font-size:0.7rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; }
  .priority-low    { background:rgba(255,255,255,0.05);  color:rgba(255,255,255,0.6); border:1px solid rgba(255,255,255,0.1); padding:3px 10px; border-radius:100px; font-size:0.7rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; }

  /* ── Buttons ── */
  .btn-complete {
    background: var(--hc-gold); color: #050a09; border: none;
    padding: 10px 20px; border-radius: 12px; font-weight: 600;
    cursor: pointer; transition: all 0.2s;
    display: flex; align-items: center; gap: 8px;
  }
  .btn-complete:hover  { background: #F2C67D; transform: scale(1.02); }
  .btn-complete:active { transform: scale(0.98); }

  .btn-ghost {
    background: rgba(255,255,255,0.05); color:#fff;
    border: 1px solid rgba(255,255,255,0.1);
    padding: 10px 20px; border-radius: 12px; font-weight: 500;
    cursor: pointer; transition: all 0.2s;
    display: flex; align-items: center; gap: 8px; font-family: 'Inter', sans-serif;
  }
  .btn-ghost:hover { background: rgba(255,255,255,0.1); border-color: rgba(255,255,255,0.2); }
  .btn-ghost.active { background: rgba(225, 175, 104,0.1); border-color: rgba(225, 175, 104,0.3); color: var(--hc-gold); }

  /* ── Icon box ── */
  .icon-box {
    width: 44px; height: 44px; border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    background: rgba(225, 175, 104,0.1); color:var(--hc-gold); font-size:1.25rem;
    flex-shrink: 0;
  }

  /* ── Sidebar ── */
  .sidebar-item {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 16px; border-radius: 12px;
    color: rgba(255,255,255,0.6); cursor: pointer; transition: all 0.2s;
  }
  .sidebar-item:hover  { background:rgba(255,255,255,0.05); color:#fff; }
  .sidebar-item.active { background:rgba(225, 175, 104,0.1);   color:var(--hc-gold); }

  /* ── Search input ── */
  .search-input {
    background:none; border:none; color:#fff; outline:none; width:200px;
    font-family:'Inter',sans-serif; font-size:0.9rem;
  }
  .search-input::placeholder { color:rgba(255,255,255,0.3); }

  /* ── Dropdown select ── */
  .filter-select {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.1);
    color: #e2e8f0; border-radius: 12px;
    padding: 9px 14px; cursor: pointer; outline: none;
    font-family: 'Inter', sans-serif; font-size: 0.88rem;
    appearance: none; -webkit-appearance: none;
    padding-right: 32px;
    transition: border-color 0.2s;
  }
  .filter-select:focus  { border-color: rgba(225, 175, 104,0.4); }
  .filter-select option { background: #0b1120; }

  /* ── Form inputs (modal) ── */
  .form-input, .form-select, .form-textarea {
    width: 100%;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 12px;
    color: #e2e8f0;
    padding: 12px 16px;
    font-family: 'Inter', sans-serif;
    font-size: 0.92rem;
    outline: none;
    transition: border-color 0.2s;
  }
  .form-input:focus, .form-select:focus, .form-textarea:focus {
    border-color: rgba(225, 175, 104,0.5);
    background: rgba(255,255,255,0.06);
  }
  .form-input::placeholder, .form-textarea::placeholder { color:rgba(255,255,255,0.3); }
  .form-select option { background:#0b1120; }
  .form-textarea { resize: vertical; min-height: 90px; }

  /* ── Modal overlay ── */
  .modal-overlay {
    position: fixed; inset:0;
    background: rgba(0,0,0,0.7);
    backdrop-filter: blur(6px);
    display: flex; align-items: center; justify-content: center;
    z-index: 999;
    animation: fadeIn 0.2s ease;
  }
  .modal-box {
    background: #0b1120;
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 24px;
    padding: 36px;
    width: 100%; max-width: 520px;
    animation: scaleIn 0.25s cubic-bezier(0.4,0,0.2,1);
  }

  /* ── Filter panel ── */
  .filter-panel {
    background: rgba(11,17,32,0.97);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 20px;
    position: absolute;
    top: calc(100% + 8px);
    right: 0;
    width: 320px;
    z-index: 100;
    animation: scaleIn 0.2s cubic-bezier(0.4,0,0.2,1);
    box-shadow: 0 24px 48px rgba(0,0,0,0.5);
  }

  .tab-underline { position:absolute; bottom:-13px; left:0; right:0; background:var(--hc-gold); height:2px; border-radius:2px; }

  h1,h2,h3 { font-family:'Inter',sans-serif; font-weight:700; }

  .req-card { animation: slideIn 0.35s ease forwards; }
  /* 05 spec: an emergency, and a job whose proof photo was rejected, must not be missed. */
  .req-card--emergency { border-color: rgba(229,72,77,0.7); background: rgba(229,72,77,0.07); }
  .req-card--alert { border-color: rgba(229,72,77,0.55); box-shadow: inset 3px 0 0 rgba(229,72,77,0.85); }
  .req-open { background: none; border: 0; padding: 0; margin: 0; color: inherit; font: inherit; cursor: pointer; text-align: left; }
  .req-open:hover { text-decoration: underline; text-underline-offset: 3px; text-decoration-color: rgba(225,175,104,0.55); }
  .req-open:focus-visible { outline: 2px solid var(--hc-gold); outline-offset: 3px; border-radius: 4px; }
  .req-card:hover .delete-btn { opacity:1; }
  .delete-btn { opacity:0.3; transition: opacity 0.2s, color 0.2s; }
  .delete-btn:hover { opacity:1 !important; color:var(--hc-urgent) !important; }

  scrollbar-width: thin;
  ::-webkit-scrollbar { width:6px; }
  ::-webkit-scrollbar-track { background:transparent; }
  ::-webkit-scrollbar-thumb { background:rgba(255,255,255,0.1); border-radius:3px; }

  /* ── History table ── */
  /* Below ~720px the six columns cannot fit; the wrapper scrolls rather than
     squeezing "In Progress" or the timestamp onto two lines. */
  .history-table { width:100%; min-width:720px; border-collapse:separate; border-spacing:0; }
  .history-table th:first-child, .history-table td:first-child,
  .history-table th:last-child,  .history-table td:last-child { white-space:nowrap; }
  /* Type is the row's identifier - give it room so long names wrap to two
     lines instead of three. */
  .history-table th:nth-child(2), .history-table td:nth-child(2) { min-width:190px; }
  .history-desc { max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .history-filters { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }

  /* Narrower than the table can fit, side-scrolling would push Status - the
     one column a manager is looking for - off screen. Each row becomes a card
     instead, labels drawn from the th text via data-label. */
  @media (max-width: 760px) {
    .history-table, .history-table tbody, .history-table tr, .history-table td { display:block; width:100%; }
    .history-table { min-width:0; }
    .history-table thead { display:none; }
    .history-table th:nth-child(2), .history-table td:nth-child(2) { min-width:0; }
    .history-table tr {
      background: rgba(255,255,255,0.02);
      border: 1px solid rgba(255,255,255,0.07);
      border-radius: 12px; margin-bottom: 10px; padding: 4px 0;
    }
    .history-table tr:hover td { background: transparent; }
    .history-table td {
      display:flex; align-items:center; justify-content:space-between; gap:14px;
      padding: 7px 14px; border-bottom: none; text-align: right;
    }
    .history-table td::before {
      content: attr(data-label);
      flex: 0 0 auto; text-align: left;
      font-size: 0.68rem; letter-spacing: 0.08em; text-transform: uppercase;
      color: rgba(255,255,255,0.3); font-weight: 600; font-style: normal;
    }
    .history-desc { max-width:none; white-space:normal; overflow:visible; }
  }
  .history-table th {
    text-align:left; padding:12px 16px; font-size:0.78rem;
    color:rgba(255,255,255,0.4); text-transform:uppercase; letter-spacing:0.05em;
    border-bottom:1px solid rgba(255,255,255,0.06); font-weight:600;
  }
  .history-table td {
    padding:14px 16px; font-size:0.88rem;
    border-bottom:1px solid rgba(255,255,255,0.04);
    color:rgba(255,255,255,0.7);
  }
  .history-table tr:hover td { background:rgba(255,255,255,0.02); }

  /* ── Guest card in dashboard ── */
  .guest-card {
    background: rgba(255,255,255,0.03);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 20px;
    transition: all 0.3s ease;
  }
  .guest-card:hover {
    border-color: rgba(225, 175, 104,0.25);
    background: rgba(255,255,255,0.05);
  }

  /* ── Guest Form Styles ── */
  .guest-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 8px;
    border-radius: 4px;
    background: rgba(225, 175, 104,0.12);
    color: var(--hc-gold);
    font-size: 0.75rem;
  }
  .guest-tag-remove {
    background: none; border: none; color: rgba(225, 175, 104,0.5); cursor: pointer; padding: 0; font-size: 14px;
  }
  .tag-input-wrap {
    display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 10px; border-radius: 12px;
    border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.04);
    min-height: 44px; align-items: center;
  }
  .chip-btn {
    padding: 6px 12px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1);
    background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.6);
    font-size: 0.75rem; cursor: pointer; transition: all 0.2s;
  }
  .chip-btn.active {
    background: rgba(225, 175, 104,0.15); border-color: var(--hc-gold); color: var(--hc-gold);
  }

  /* ── Analytics ── */
  .kpi-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 18px;
    padding: 22px 24px;
    flex: 1;
    min-width: 140px;
  }
  .staff-perf-row {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 16px;
    transition: border-color 0.2s;
    overflow: hidden;
  }
  .staff-perf-row:hover { border-color: rgba(225, 175, 104,0.25); }
  .activity-row { border-bottom: 1px solid rgba(255,255,255,0.04); }
  .activity-row:last-child { border-bottom: none; }
  .activity-scroll { overflow-x: auto; }
  .activity-grid {
    display: grid;
    grid-template-columns: 78px minmax(150px, 1fr) 104px 148px 148px 88px;
    min-width: 716px;
  }
  @keyframes expandDown {
    from { opacity: 0; transform: translateY(-6px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .log-panel { animation: expandDown 0.2s ease; }

  /* Layout Classes */
  /* The same stone ground as the marketing site and the guest concierge, so a
     GM who saw the website recognises the product as the same brand. The grain
     is fixed and non-interactive; everything else stacks above it. */
  .app-container {
    position: relative;
    display: flex;
    min-height: 100vh;
    color: var(--clefs-ink);
    background-color: var(--clefs-slate-900);
    background-image:
      radial-gradient(1200px 700px at 78% -10%, rgba(225,175,104,0.06), transparent 66%),
      linear-gradient(180deg, var(--clefs-slate-850), var(--clefs-slate-900) 60%, var(--clefs-slate-950));
  }
  .app-container::before {
    content: ''; position: fixed; inset: 0; z-index: 0; pointer-events: none;
    background-image: url('/brand/slate-grain.png'); background-size: 256px 256px;
    opacity: 0.4;
  }
  .app-container > * { position: relative; z-index: 1; }

  .sidebar-aside {
    width: 260px;
    border-right: 1px solid rgba(255,255,255,0.05);
    padding: 32px 20px;
    display: flex;
    flex-direction: column;
    gap: 40px;
    height: 100vh;
    position: sticky;
    top: 0;
    flex-shrink: 0;
    background: rgba(3, 6, 12, 0.55);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-right-color: var(--clefs-line-soft);
    transition: transform 0.3s ease;
    z-index: 1000;
    overflow-y: auto;
    scrollbar-width: none; /* Hide scrollbar for clean look */
  }
  .sidebar-aside::-webkit-scrollbar {
    display: none;
  }

  .main-content {
    flex: 1;
    padding: 48px 48px 80px;
    min-width: 0;
  }

  /* ── Header ── */
  .dashboard-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 40px;
    flex-wrap: wrap;
    gap: 20px;
  }
  .header-controls {
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
  }

  /* Hamburger Menu Button */
  .menu-toggle-btn {
    display: none;
    background: none;
    border: none;
    color: #fff;
    cursor: pointer;
    padding: 8px;
    align-items: center;
    justify-content: center;
  }

  /* Mobile Top Navbar */
  .mobile-top-bar {
    display: none;
    height: 60px;
    background: rgba(11,17,32,0.97);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid rgba(255,255,255,0.05);
    padding: 0 16px;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 990;
  }

  .sidebar-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.6);
    backdrop-filter: blur(4px);
    z-index: 995;
    animation: fadeIn 0.2s ease;
  }

  /* Stats Grid */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 20px;
    margin-bottom: 44px;
  }

  /* Department Tabs */
  .dept-tabs-container {
    display: flex;
    gap: 12px;
    overflow-x: auto;
    margin-bottom: 24px;
    padding-bottom: 12px;
    scrollbar-width: thin;
    -webkit-overflow-scrolling: touch;
  }
  .dept-tabs-container::-webkit-scrollbar {
    height: 4px;
  }
  .dept-tabs-container::-webkit-scrollbar-thumb {
    background: rgba(225, 175, 104,0.2);
    border-radius: 2px;
  }

  /* Status Tabs */
  .status-tabs-container {
    display: flex;
    gap: 28px;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    margin-bottom: 28px;
    padding-bottom: 12px;
  }

  /* Request Card layout defaults */
  .req-card {
    padding: 24px;
    display: flex;
    align-items: center;
    gap: 20px;
  }

  /* Responsive Design overrides */
  /* A 260px rail costs too much of a portrait tablet, so the drawer takes over
     at 1024 rather than 768 - iPad Pro portrait included. */
  @media (max-width: 1024px) {
    .stats-grid { grid-template-columns: repeat(2, 1fr); }

    .app-container { flex-direction: column; }

    .mobile-top-bar { display: flex; }

    .sidebar-aside {
      position: fixed;
      left: 0;
      top: 0;
      bottom: 0;
      transform: translateX(-100%);
      height: 100%;
    }

    .sidebar-aside.open { transform: translateX(0); }

    .main-content { padding: 32px 28px 80px; }

    .menu-toggle-btn { display: flex; }
  }

  @media (max-width: 768px) {
    .main-content { padding: 24px 16px 80px; }
  }

  @media (max-width: 640px) {
    .req-card {
      flex-direction: column;
      align-items: flex-start;
      gap: 14px;
      padding: 16px;
    }
    .req-card > div {
      width: 100%;
    }
    .req-card-status-info {
      display: flex !important;
      flex-direction: row !important;
      align-items: center !important;
      justify-content: space-between !important;
      border-top: 1px solid rgba(255,255,255,0.05) !important;
      padding-top: 12px !important;
      padding-right: 0 !important;
      margin-top: 4px !important;
      width: 100% !important;
      text-align: left !important;
    }
    .req-card-status-info > div {
      margin-bottom: 0 !important;
      justify-content: flex-start !important;
    }
    .req-card-actions {
      display: flex !important;
      width: 100% !important;
      justify-content: flex-end !important;
      gap: 8px !important;
      border-top: 1px solid rgba(255,255,255,0.05) !important;
      padding-top: 12px !important;
      margin-top: 4px !important;
    }
  }

  @media (max-width: 600px) {
    .status-tabs-container {
      gap: 0;
      justify-content: space-between;
    }
    .status-tab {
      flex: 1;
      text-align: center;
      font-size: 0.8rem !important;
    }
  }

  @media (max-width: 480px) {
    /* Two up, not one - four full-bleed tiles pushed the charts off screen. */
    .stats-grid {
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
      margin-bottom: 24px;
    }
    .sd-stat { padding: 16px 14px !important; }
    .sd-stat__value { font-size: 1.75rem; }
    .sd-stat__label { margin-bottom: 8px; font-size: 0.66rem; letter-spacing: 0.1em; }
    .dashboard-header {
      flex-direction: column;
      align-items: stretch;
      margin-bottom: 24px;
      gap: 16px;
    }
    .dashboard-header > div {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .header-controls {
      flex-direction: column;
      align-items: stretch;
      width: 100%;
    }
    .header-controls > div {
      width: 100% !important;
    }
    .header-controls input {
      width: 100% !important;
    }
    .header-controls button {
      width: 100% !important;
      justify-content: center;
    }
    .modal-box {
      padding: 20px;
      margin: 16px;
      max-height: calc(100vh - 32px);
      overflow-y: auto;
    }
  }

  /* ── Insight band ──
     A wide chart and a narrow one; they collapse to one column before the
     hourly columns get too thin to read. */
  .insight-grid {
    display: grid;
    grid-template-columns: 1.32fr 1fr;
    gap: 20px;
    align-items: stretch;   /* cards in a row end on the same line */
  }
  .insight-grid--even { grid-template-columns: 1fr 1fr; }
  @media (max-width: 1100px) {
    .insight-grid, .insight-grid--even { grid-template-columns: 1fr; }
  }

  .insight-toggle {
    background: none; border: none; padding: 0; cursor: pointer;
    color: rgba(242,238,230,0.4); font-family: 'Inter', sans-serif;
    font-size: 0.78rem; display: flex; align-items: center; gap: 6px;
    transition: color 0.2s;
  }
  .insight-toggle:hover { color: var(--hc-gold); }

  /* ── Shared responsive layouts ──────────────────────────────────────── */

  /* Staff leaderboard: a right-aligned strip on a desk monitor, an even grid
     once there is no longer room to keep seven figures on one line. */
  .staff-stats {
    display: flex; gap: 24px; flex-wrap: wrap; flex: 1;
    justify-content: flex-end; align-items: center;
  }
  @media (max-width: 980px) {
    .staff-stats {
      display: grid; grid-template-columns: repeat(4, 1fr);
      gap: 16px 10px; width: 100%; flex: none; justify-content: stretch;
      padding-top: 14px; border-top: 1px solid rgba(255,255,255,0.05);
    }
    .staff-stats > div { text-align: center; }
    /* The chevron is an affordance, not a figure - give it its own full-width
       row instead of leaving it orphaned in a data cell. */
    .staff-stats > :last-child { grid-column: 1 / -1; display: flex; justify-content: center; }
  }
  @media (max-width: 560px) { .staff-stats { grid-template-columns: repeat(3, 1fr); } }

  .duty-legend { display: grid; grid-template-columns: repeat(auto-fit, minmax(158px, 1fr)); gap: 16px; }

  .timeline-grid { display: grid; grid-template-columns: 1fr 2fr; gap: 30px; }
  @media (max-width: 900px) { .timeline-grid { grid-template-columns: 1fr; gap: 20px; } }

  .modal-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 480px) { .modal-grid { grid-template-columns: 1fr; } }

  /* Headers that sit opposite a control cluster */
  .view-header {
    display: flex; justify-content: space-between; align-items: flex-start;
    margin-bottom: 40px; flex-wrap: wrap; gap: 20px;
  }
  .view-header h1 { font-size: clamp(1.7rem, 4.2vw, 2.4rem); margin-bottom: 8px; }
  @media (max-width: 768px) { .view-header { margin-bottom: 26px; } }

${chartStyles}
`;

/* ─────────────────────────── Constants ─────────────────────────────── */
// Same-origin proxy (next.config rewrite): the session/device cookie rides along.
const API_BASE = API_PREFIX;

/**
 * SSE event fired when the voice assistant files a request. Renamed during the
 * Aria -> Clefs rebrand. The API emits the legacy name alongside this one so
 * older dashboard builds keep working - listen for exactly one, or the toast
 * and alert sound fire twice.
 */
const ASSISTANT_REQUEST_EVENT = "new_clefs_request";

/** One release of grace for the old ManualEntryModal (03 spec 1.8). */
const CLASSIC_ENTRY = process.env.NEXT_PUBLIC_SERVICE_CLASSIC_ENTRY === "1";

/**
 * Placeholder categories meaning "the assistant did not classify this request",
 * which the dashboard replaces with the department mapping. "Aria Request" is the
 * pre-rebrand value and still exists on historical rows.
 */
const UNCLASSIFIED_ASSISTANT_CATEGORIES = ["Clefs Request", "Aria Request"];

function isUnclassifiedAssistantCategory(category: string | null | undefined): boolean {
  return !!category && UNCLASSIFIED_ASSISTANT_CATEGORIES.includes(category);
}

const REQUEST_TYPES = [
  "Towel",
  "Clean Sheets",
  "In-Room Dining",
  "Water Supply",
  "Wake-Up Call",
  "Maintenance",
  "Luggage Assistance",
  "Spa Booking",
  "Billing",
  "Direct Conversation",
  "Front Desk",
  "Other",
];

const CATEGORIES: Record<string, string> = {
  "Towel": "Housekeeping",
  "Clean Sheets": "Housekeeping",
  "Cleaning": "Housekeeping",
  "In-Room Dining": "Food & Beverage",
  "Food": "Food & Beverage",
  "Water Supply": "Maintenance",
  "Water": "Housekeeping",
  "Wake-Up Call": "Front Desk",
  "Maintenance": "Maintenance",
  "Luggage Assistance": "Concierge",
  "Spa Booking": "Concierge",
  "Billing": "Front Desk",
  "Direct Conversation": "Front Desk",
  "Checkout": "Front Desk",
  "Discount": "Front Desk",
  "Front Desk": "Front Desk",
  "Other": "General",
};

const DEPARTMENTS = ["Housekeeping", "Food & Beverage", "Maintenance", "Front Desk", "Concierge", "General"];

const STAY_ICONS: Record<string, string> = {
  solo: "👤",
  couple: "💑",
  family: "👨‍👩‍👧‍👦",
  business: "💼",
  group: "👥",
};

const STAY_TYPES = ["solo", "couple", "family", "business", "group"];
const LOYALTY_TIERS = ["member", "discoverist", "explorist", "globalist"];
const GUEST_TYPES = ["primary", "spouse", "child", "relative", "colleague", "friend"];
const OCCASION_OPTIONS = ["birthday", "anniversary", "honeymoon", "business trip", "celebration"];

const TIER_COLORS: Record<string, string> = {
  member: "#8B8B8B",
  discoverist: "var(--hc-blue-muted)",
  explorist: "#B388D9",
  globalist: "var(--hc-gold)",
};

function getIcon(type: string, category: string = "") {
  const t = type.toLowerCase();
  const c = category.toLowerCase();
  if (c.includes("housekeeping") || t.includes("towel") || t.includes("sheet") || t.includes("clean")) return <MdOutlineCleaningServices />;
  if (c.includes("food") || c.includes("dining") || t.includes("dining") || t.includes("food")) return <MdOutlineRoomService />;
  if (c.includes("maintenance") || t.includes("water")) return <MdOutlineWaterDrop />;
  if (c.includes("concierge") || c.includes("front") || t.includes("luggage") || t.includes("spa") || t.includes("booking")) return <MdOutlineMiscellaneousServices />;
  return <MdOutlineHotel />;
}

/* ─────────────────────── TypeScript interfaces ───────────────────────── */
// v2 fields (state, SLA due times, source, language, original text, reopen
// count, escalation, guest confirmation) come from V2Fields; all optional,
// so pre-v2 rows and an older server still type-check.
interface Request extends V2Fields {
  id: number;
  room: string;
  type: string;
  subtype?: string;        // e.g. "Club Sandwich" for In-Room Dining
  description: string;
  timestamp: number;       // ms since epoch
  status: "new" | "progress" | "completed";
  category: string;
  priority: "high" | "medium" | "low";
  assigned_to?: string;
  accepted_at?: number;
  resolved_at?: number;
}

interface ManualFormState {
  room: string;
  type: string;
  subtype: string;
  description: string;
  priority: "high" | "medium" | "low";
}

interface GuestRoom {
  roomNumber: string;
  guests?: { name: string; type: string; age?: number }[];
  stayType?: string;
  checkIn?: string;
  checkOut?: string;
  preferences?: {
    tags?: string[];
    language?: string;
  };
  loyaltyTier?: string;
  occasions?: string[];
}

interface StaffMember {
  id: number;
  name: string;
  role: string;
  department: string;
  is_active: boolean;
  is_online: boolean;
  phone_number?: string;
}

interface ActivityEntry {
  id: number;
  room: string;
  type: string;
  subtype?: string;
  category: string;
  priority: string;
  status: string;
  notified_at?: number;
  done_at?: number;
  resolution_mins?: number;
}

interface StaffAnalytics extends StaffMember {
  total_completed: number;
  completed_today: number;
  avg_resolution_mins?: number;
  fastest_mins?: number;
  slowest_mins?: number;
  currently_active: number;
  today_work_mins: number;
  total_work_mins: number;
  task_type_breakdown: Record<string, number>;
  activity_log: ActivityEntry[];
}

interface HotelKPIs {
  total_completed: number;
  completed_today: number;
  currently_active: number;
  avg_resolution_mins?: number;
}

interface AnalyticsData {
  hotel_kpis: HotelKPIs;
  staff: StaffAnalytics[];
}

type SidebarView = "dashboard" | "guests" | "history" | "staff" | "analytics" | "room-timeline";

/* ─────────────────────── API helpers ───────────────────────── */

/** The wall clock, read where the old code read `new Date()` (filters and
    chart buckets are recomputed on every data change, as before). */
function wallClock(): number {
  return Date.now();
}

/** Assistant placeholder categories become the department mapping (as before). */
function cleanCategory(r: Request): Request {
  return { ...r, category: isUnclassifiedAssistantCategory(r.category) ? CATEGORIES[r.type] || "General" : r.category };
}

/* The legacy PATCH as a stand-in for a v2 transition, for legacy open-access
   visitors (the v2 routes need a signed-in member or a device). The server
   maps it onto the same state machine (service README, "Legacy PATCH"). */
async function legacyPatch(hotelSlug: string, req: Request, action: TransitionAction, staffName?: string): Promise<boolean> {
  let body: Record<string, unknown> | null = null;
  if (action === "complete") body = { status: "completed", resolved_at: wallClock() };
  else if ((action === "assign" || action === "reassign") && staffName) body = { status: "progress", assigned_to: staffName, accepted_at: wallClock() };
  else if (action === "ack" && req.assigned_to) body = { status: "progress", assigned_to: req.assigned_to };
  else if (action === "unassign") body = { status: "new" };
  else if (action === "reopen") body = { status: "progress" };
  if (!body) return false;
  try {
    await apiFetch(`/hotels/${hotelSlug}/requests/${req.id}`, { method: "PATCH", json: body, silent401: true });
    return true;
  } catch { return false; }
}

/* Requests: the v2 queue (every legacy field plus the v2 ones) over the last
   7 days, open work always included by the server. Falls back to the legacy
   list (also windowed with ?since=) on a server without the v2 route. The
   queue sorts by urgency; the dashboard keeps its newest-first order. */
const REQUEST_WINDOW_MS = 7 * 86_400_000;

async function fetchRequests(hotelSlug: string): Promise<Request[]> {
  const since = Date.now() - REQUEST_WINDOW_MS;
  try {
    const [open, recent] = await Promise.all([
      fetchQueue(hotelSlug, { state: "open", limit: 500 }),
      fetchQueue(hotelSlug, { state: "all", since, limit: 500 }),
    ]);
    const byId = new Map<number, Request>();
    for (const r of [...recent, ...open]) byId.set(r.id, r as unknown as Request);
    return [...byId.values()].sort((a, b) => b.timestamp - a.timestamp);
  } catch (err) {
    // Legacy open-access visitors (and older servers) read the legacy list.
    if (!takesLegacyPath(err)) return [];
  }
  try {
    return await apiFetch<Request[]>(`/hotels/${hotelSlug}/requests`, { query: { since }, silent401: true });
  } catch { return []; }
}

async function fetchGuestRooms(hotelSlug: string): Promise<Record<string, GuestRoom>> {
  try {
    return await apiFetch<Record<string, GuestRoom>>(`/hotels/${hotelSlug}/guests`);
  } catch { return {}; }
}

async function fetchStaff(hotelSlug: string): Promise<StaffMember[]> {
  try {
    return await apiFetch<StaffMember[]>(`/hotels/${hotelSlug}/staff`);
  } catch { return []; }
}

async function fetchAnalytics(hotelSlug: string): Promise<AnalyticsData | null> {
  try {
    return await apiFetch<AnalyticsData>(`/hotels/${hotelSlug}/staff-analytics`);
  } catch { return null; }
}

/* ─────────────────────── Time helper ────────────────────────────────── */
function relativeTime(ts: number): string {
  const diff = Math.floor((Date.now() - ts) / 1000);
  if (diff < 60) return "Just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} mins ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} hrs ago`;
  return `${Math.floor(diff / 86400)} days ago`;
}

/* Hotel locale + timezone (useFormat() at the call site), not "en-IN" and
   the browser's zone: a Dubai property read from anywhere shows Dubai time. */
function formatTimestamp(ts: number, tz?: string, locale?: string): string {
  return formatDateTime(ts, tz, locale, "short");
}

function formatDateShort(dateStr: string, tz?: string, locale?: string): string {
  return formatDate(dateStr, tz, locale, "short");
}

function resColor(mins?: number): string {
  if (mins == null) return "rgba(255,255,255,0.3)";
  if (mins <= 10) return "var(--hc-success)";
  if (mins <= 30) return "var(--hc-pending)";
  return "#ef4444";
}

function fmtMins(mins?: number): string {
  if (mins == null) return "-";
  if (mins < 1) return "<1 min";
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${Math.round(mins % 60)}m`;
}

/* ═══════════════════════ Sub-components ════════════════════════════ */

/* ── Guest Profile Modal ── */
interface GuestModalProps {
  initial?: GuestRoom;
  onClose: () => void;
  onSave: (data: GuestRoom) => void;
}

function GuestModal({ initial, onClose, onSave }: GuestModalProps) {
  const [form, setForm] = useState<GuestRoom>(() => {
    if (initial) {
      return {
        ...initial,
        guests: initial.guests && initial.guests.length > 0 ? initial.guests : [{ name: "", type: "primary", age: undefined }],
        preferences: { tags: initial.preferences?.tags || [], language: initial.preferences?.language || "" },
        occasions: initial.occasions || [],
        checkIn: initial.checkIn || "",
        checkOut: initial.checkOut || "",
      };
    }
    return {
      roomNumber: "",
      guests: [{ name: "", type: "primary", age: undefined }],
      stayType: "solo",
      checkIn: "",
      checkOut: "",
      preferences: { tags: [], language: "" },
      loyaltyTier: "member",
      occasions: [],
    };
  });

  const [preferenceInput, setPreferenceInput] = useState("");

  const addGuest = () => setForm(f => ({ ...f, guests: [...(f.guests || []), { name: "", type: "relative", age: undefined }] }));
  const removeGuest = (idx: number) => setForm(f => ({ ...f, guests: f.guests?.filter((_, i) => i !== idx) }));
  const updateGuest = (idx: number, field: string, val: any) => setForm(f => ({
    ...f, guests: f.guests?.map((g, i) => (i === idx ? { ...g, [field]: val } : g))
  }));

  const toggleOccasion = (occ: string) => setForm(f => ({
    ...f, occasions: f.occasions?.includes(occ) ? f.occasions.filter(o => o !== occ) : [...(f.occasions || []), occ]
  }));

  const addTag = (val: string) => {
    if (!val.trim()) return;
    setForm(f => ({
      ...f, preferences: { ...f.preferences!, tags: [...(f.preferences?.tags || []), val.trim()] }
    }));
    setPreferenceInput("");
  };

  const removeTag = (idx: number) => setForm(f => ({
    ...f, preferences: { ...f.preferences!, tags: f.preferences?.tags?.filter((_, i) => i !== idx) }
  }));

  return (
    <div className="modal-overlay" onClick={onClose} style={{ zIndex: 1001 }}>
      <div className="modal-box" onClick={e => e.stopPropagation()} style={{ maxWidth: "580px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "24px" }}>
          <div>
            <h2 style={{ fontSize: "1.6rem", marginBottom: "6px" }}>{initial ? `Edit Room ${initial.roomNumber}` : "Add Guest Profile"}</h2>
            <p style={{ color: "rgba(255,255,255,0.4)", fontSize: "0.88rem" }}>Add guest preferences and stay details</p>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "rgba(255,255,255,0.4)", cursor: "pointer" }}>
            <LuXClose size={22} />
          </button>
        </div>

        <div style={{ maxHeight: "60vh", overflowY: "auto", paddingRight: "10px", display: "flex", flexDirection: "column", gap: "18px" }}>
          {/* Room # */}
          <div>
            <label className="label" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "8px" }}>ROOM NUMBER</label>
            <input className="form-input" placeholder="e.g. 502" value={form.roomNumber} onChange={e => setForm(f => ({ ...f, roomNumber: e.target.value }))} />
          </div>

          {/* Stay Type & Loyalty */}
          <div className="modal-grid">
            <div>
              <label className="label" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "8px" }}>STAY TYPE</label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                {STAY_TYPES.map(t => (
                  <button key={t} className={`chip-btn ${form.stayType === t ? 'active' : ''}`} onClick={() => setForm(f => ({ ...f, stayType: t }))}>{STAY_ICONS[t]} {t}</button>
                ))}
              </div>
            </div>
            <div>
              <label className="label" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "8px" }}>LOYALTY TIER</label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                {LOYALTY_TIERS.map(t => (
                  <button key={t} className={`chip-btn ${form.loyaltyTier === t ? 'active' : ''}`} style={form.loyaltyTier === t ? { background: `${TIER_COLORS[t]}22`, color: TIER_COLORS[t], borderColor: TIER_COLORS[t] } : {}} onClick={() => setForm(f => ({ ...f, loyaltyTier: t }))}>{t}</button>
                ))}
              </div>
            </div>
          </div>

          {/* Check-in / Check-out */}
          <div className="modal-grid">
            <div>
              <label className="label" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "8px" }}>CHECK-IN DATE <span style={{ color: "#ef4444" }}>*</span></label>
              <input
                type="date"
                className="form-input"
                required
                value={form.checkIn || ""}
                onChange={e => setForm(f => ({ ...f, checkIn: e.target.value }))}
              />
            </div>
            <div>
              <label className="label" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "8px" }}>CHECK-OUT DATE <span style={{ color: "#ef4444" }}>*</span></label>
              <input
                type="date"
                className="form-input"
                required
                value={form.checkOut || ""}
                onChange={e => setForm(f => ({ ...f, checkOut: e.target.value }))}
              />
            </div>
          </div>

          {/* Language */}
          <div>
            <label className="label" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "8px" }}>PREFERRED LANGUAGE <span style={{ opacity: 0.4 }}>(optional)</span></label>
            <input
              className="form-input"
              placeholder="e.g. Hindi, Bengali. Leave blank for English default"
              value={form.preferences?.language || ""}
              onChange={e => setForm(f => ({ ...f, preferences: { ...f.preferences!, language: e.target.value } }))}
            />
          </div>

          {/* Guests */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px" }}>
              <label className="label" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)" }}>GUESTS</label>
              <button onClick={addGuest} className="btn-ghost" style={{ padding: "4px 10px", fontSize: "0.78rem", borderRadius: "8px" }}><LuPlus size={12} /> Add Guest</button>
            </div>
            {form.guests?.map((g, i) => (
              <div key={i} style={{ display: "flex", gap: "8px", marginBottom: i < (form.guests?.length || 0) - 1 ? "8px" : 0 }}>
                <input className="form-input" style={{ flex: 2 }} placeholder="Name" value={g.name} onChange={e => updateGuest(i, "name", e.target.value)} />
                <select className="form-select" style={{ flex: 1.5 }} value={g.type} onChange={e => updateGuest(i, "type", e.target.value)}>
                  {GUEST_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
                <input className="form-input" style={{ width: "65px" }} placeholder="Age" value={g.age || ""} onClick={(e: any) => e.target.select()} onChange={e => updateGuest(i, "age", e.target.value ? parseInt(e.target.value) : undefined)} />
                {form.guests!.length > 1 && <button onClick={() => removeGuest(i)} style={{ background: "none", border: "none", color: "rgba(239,68,68,0.4)", cursor: "pointer" }}><LuTrash2 size={16} /></button>}
              </div>
            ))}
          </div>

          {/* Preferences */}
          <div>
            <label className="label" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "8px" }}>PREFERENCES & REQUESTS</label>
            <div className="tag-input-wrap">
              {form.preferences?.tags?.map((tag, i) => (
                <span key={i} className="guest-tag" style={{ background: "rgba(225, 175, 104,0.12)", color: "var(--hc-gold)" }}>
                  {tag} <button className="guest-tag-remove" onClick={() => removeTag(i)}>×</button>
                </span>
              ))}
              <input 
                className="search-input" 
                style={{ width: "100%", fontSize: "0.85rem" }} 
                placeholder="e.g. Veg food, dance, travel, extra pillows..." 
                value={preferenceInput} 
                onChange={e => setPreferenceInput(e.target.value)} 
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addTag(preferenceInput);
                  }
                }} 
              />
            </div>
          </div>

          {/* Occasions */}
          <div>
            <label className="label" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "10px" }}>OCCASIONS</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
              {OCCASION_OPTIONS.map(o => (
                <button key={o} className={`chip-btn ${form.occasions?.includes(o) ? 'active' : ''}`} onClick={() => toggleOccasion(o)}><LuHeart size={12} style={{ marginRight: "4px" }} /> {o}</button>
              ))}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: "12px", marginTop: "32px" }}>
          <button onClick={onClose} className="btn-ghost">Cancel</button>
          <button
            onClick={() => {
              if (!form.checkIn || !form.checkOut) return;
              const cleaned = { ...form, preferences: { ...form.preferences!, language: form.preferences?.language?.trim() || undefined } };
              onSave(cleaned);
            }}
            className="btn-complete"
            disabled={!form.checkIn || !form.checkOut}
          ><LuCheck size={18} /> {initial ? "Update Profile" : "Save Profile"}</button>
        </div>
      </div>
    </div>
  );
}

/* ── Manual Entry Modal ── */
interface ManualEntryModalProps {
  onClose: () => void;
  onSubmit: (req: Omit<Request, "id" | "timestamp" | "status" | "category">) => void;
}

function ManualEntryModal({ onClose, onSubmit }: ManualEntryModalProps) {
  const [form, setForm] = useState<ManualFormState>({
    room: "", type: REQUEST_TYPES[0], subtype: "", description: "", priority: "medium"
  });
  const [errors, setErrors] = useState<Partial<ManualFormState>>({});

  const validate = () => {
    const e: Partial<ManualFormState> = {};
    if (!form.room.trim()) e.room = "Room number is required";
    if (!form.description.trim()) e.description = "Description is required";
    return e;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const errs = validate();
    if (Object.keys(errs).length) { setErrors(errs); return; }
    onSubmit({
      room: form.room.trim(),
      type: form.type,
      subtype: form.subtype.trim() || undefined,
      description: form.description.trim(),
      priority: form.priority,
    });
    onClose();
  };

  const field = (key: keyof ManualFormState, val: string) =>
    setForm(f => ({ ...f, [key]: val }));

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-box">
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "28px" }}>
          <div>
            <h2 style={{ fontSize: "1.6rem", marginBottom: "6px" }}>Manual Entry</h2>
            <p style={{ color: "rgba(255,255,255,0.4)", fontSize: "0.88rem" }}>Add a request yourself</p>
          </div>
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", color: "rgba(255,255,255,0.4)", cursor: "pointer", padding: "4px" }}
          >
            <BsX size={24} />
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
          {/* Room */}
          <div>
            <label style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.5)", marginBottom: "8px", display: "block" }}>ROOM NUMBER</label>
            <input
              className="form-input"
              placeholder="e.g. 302"
              value={form.room}
              onChange={e => field("room", e.target.value)}
            />
            {errors.room && <p style={{ color: "#ef4444", fontSize: "0.78rem", marginTop: "5px" }}>{errors.room}</p>}
          </div>

          {/* Type & Priority in a row */}
          <div className="modal-grid">
            <div>
              <label style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.5)", marginBottom: "8px", display: "block" }}>REQUEST TYPE</label>
              <select className="form-select" value={form.type} onChange={e => field("type", e.target.value)}>
                {REQUEST_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.5)", marginBottom: "8px", display: "block" }}>PRIORITY</label>
              <select className="form-select" value={form.priority} onChange={e => field("priority", e.target.value as any)}>
                <option value="high">🔴 High</option>
                <option value="medium">🟡 Medium</option>
                <option value="low">🔵 Low</option>
              </select>
            </div>
          </div>

          {/* Subtype */}
          <div>
            <label style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.5)", marginBottom: "8px", display: "block" }}>SUBTYPE <span style={{ opacity: 0.5 }}>(optional)</span></label>
            <input
              className="form-input"
              placeholder="e.g. Club Sandwich, Swedish Massage"
              value={form.subtype}
              onChange={e => field("subtype", e.target.value)}
            />
          </div>

          {/* Description */}
          <div>
            <label style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.5)", marginBottom: "8px", display: "block" }}>DESCRIPTION</label>
            <textarea
              className="form-textarea"
              placeholder="What does the guest need?"
              value={form.description}
              onChange={e => field("description", e.target.value)}
            />
            {errors.description && <p style={{ color: "#ef4444", fontSize: "0.78rem", marginTop: "5px" }}>{errors.description}</p>}
          </div>

          {/* Actions */}
          <div style={{ display: "flex", gap: "12px", justifyContent: "flex-end", marginTop: "8px" }}>
            <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-complete">
              <BsPlusLg /> Create Request
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ── Filter Panel ── */
interface FilterState {
  status: string;
  time: string;
  type: string;
}

interface FilterPanelProps {
  filters: FilterState;
  onChange: (f: FilterState) => void;
  onReset: () => void;
}

function FilterPanel({ filters, onChange, onReset }: FilterPanelProps) {
  const set = (key: keyof FilterState, val: string) =>
    onChange({ ...filters, [key]: val });

  const isActive = filters.status !== "all" || filters.time !== "all" || filters.type !== "all";

  return (
    <div className="filter-panel">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "18px" }}>
        <span style={{ fontWeight: "600", fontSize: "0.95rem" }}>Filters</span>
        {isActive && (
          <button
            onClick={onReset}
            style={{ background: "none", border: "none", color: "var(--hc-gold)", cursor: "pointer", fontSize: "0.8rem", display: "flex", alignItems: "center", gap: "4px" }}
          >
            <BsArrowClockwise size={12} /> Reset
          </button>
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
        {/* Status */}
        <div>
          <label style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", marginBottom: "6px", display: "block", textTransform: "uppercase", letterSpacing: "0.05em" }}>Status</label>
          <div style={{ display: "flex", position: "relative" }}>
            <select className="filter-select" style={{ width: "100%" }} value={filters.status} onChange={e => set("status", e.target.value)}>
              <option value="all">All statuses</option>
              <option value="new">New</option>
              <option value="progress">In Progress</option>
              <option value="completed">Completed</option>
            </select>
            <BsChevronDown style={{ position: "absolute", right: "12px", top: "50%", transform: "translateY(-50%)", color: "rgba(255,255,255,0.3)", pointerEvents: "none", fontSize: "0.75rem" }} />
          </div>
        </div>

        {/* Time */}
        <div>
          <label style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", marginBottom: "6px", display: "block", textTransform: "uppercase", letterSpacing: "0.05em" }}>Time Range</label>
          <div style={{ display: "flex", position: "relative" }}>
            <select className="filter-select" style={{ width: "100%" }} value={filters.time} onChange={e => set("time", e.target.value)}>
              <option value="all">All time</option>
              <option value="15min">Last 15 minutes</option>
              <option value="1hour">Last 1 hour</option>
              <option value="today">Today</option>
            </select>
            <BsChevronDown style={{ position: "absolute", right: "12px", top: "50%", transform: "translateY(-50%)", color: "rgba(255,255,255,0.3)", pointerEvents: "none", fontSize: "0.75rem" }} />
          </div>
        </div>

        {/* Type */}
        <div>
          <label style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", marginBottom: "6px", display: "block", textTransform: "uppercase", letterSpacing: "0.05em" }}>Request Type</label>
          <div style={{ display: "flex", position: "relative" }}>
            <select className="filter-select" style={{ width: "100%" }} value={filters.type} onChange={e => set("type", e.target.value)}>
              <option value="all">All types</option>
              {REQUEST_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <BsChevronDown style={{ position: "absolute", right: "12px", top: "50%", transform: "translateY(-50%)", color: "rgba(255,255,255,0.3)", pointerEvents: "none", fontSize: "0.75rem" }} />
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Request Card ── */
interface RequestCardProps {
  hotelSlug: string;
  req: Request;
  onStatusChange: (id: number, s: "new" | "progress" | "completed") => void;
  onDelete: (id: number) => void;
  /** v2: assign a staff member by id (POST .../transition), never a typed name. */
  onAssign: (id: number, staffId: number) => void;
  /** v2: any other transition (ack, start, reopen ...). */
  onAction: (id: number, action: TransitionAction) => void;
  /** Opens the request detail (photo, guest answer, timeline). */
  onOpen: (id: number) => void;
  /** Demo hotels only: move the job's clock 3 minutes so the ladder runs live. */
  onSimulate?: (id: number) => void;
  simulating?: boolean;
  staff: StaffMember[];
  now: number;
  busy: boolean;
  canManage: boolean;
}

/** The urgent ladder pages the duty manager at its second step (level 2). */
function dutyManagerPaged(req: Request): boolean {
  const step = escalationStep(req.escalation_level);
  return step === "duty_manager" || step === "gm";
}

/** "Accepted: 18:02" from the person's ack (v2), else the assignment time;
    legacy rows only have accepted_at, which was the old accept. */
function acceptedLine(req: Request): { label: string; at: number } | null {
  if (req.state) {
    if (req.acknowledged_at) return { label: "Accepted", at: req.acknowledged_at };
    const assigned = req.assigned_at ?? req.accepted_at;
    return assigned ? { label: "Assigned", at: assigned } : null;
  }
  return req.accepted_at ? { label: "Accepted", at: req.accepted_at } : null;
}

const DEPT_COLORS: Record<string, string> = {
  "Housekeeping":    "var(--hc-light-green)",
  "Food & Beverage": "var(--hc-warm-gold)",
  "Maintenance":     "var(--hc-blue-muted)",
  "Front Desk":      "var(--hc-blue)",
  "Concierge":       "var(--hc-gold)",
  "General":         "var(--hc-text-muted)",
};

function RequestCard({ hotelSlug, req, onStatusChange, onDelete, onAssign, onAction, onOpen, onSimulate, simulating, staff, now, busy, canManage }: RequestCardProps) {
  const hf = useFormat();
  const state = effectiveState(req);
  const deptColor = DEPT_COLORS[req.category] || "#94a3b8";
  const open = isOpenRow(req);
  const emergency = open && isEmergency(req);
  const accepted = acceptedLine(req);
  // 05 spec 2.7: the ladder runs on jobs nobody has accepted yet. An urgent
  // job (emergency, sensitive, L1) pages everyone at once: nothing to simulate.
  const canSimulate = Boolean(onSimulate) && (state === "new" || state === "assigned" || state === "reopened")
    && !isUrgentPath(req);
  const cardClass = `glass-card req-card${emergency ? " req-card--emergency" : req.proof_status === "rejected" ? " req-card--alert" : ""}`;

  return (
    <div
      className={cardClass}
    >
      <div className="icon-box">{getIcon(req.type, req.category)}</div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px", flexWrap: "wrap" }}>
          <button type="button" className="req-open" onClick={() => onOpen(req.id)} title="Open the request: photo, guest answer and timeline">
            <span style={{ fontWeight: "700", fontSize: "1.05rem" }}>Room {req.room}</span>
          </button>
          <span style={{ color: "rgba(255,255,255,0.25)" }}>•</span>
          <span style={{ color: "var(--hc-gold)", fontSize: "0.9rem", fontWeight: "500" }}>{req.type}</span>
          {req.subtype && (
            <span style={{ color: "rgba(255,255,255,0.4)", fontSize: "0.82rem", fontStyle: "italic" }}>· {req.subtype}</span>
          )}
          {/* URGENT is priority high (05 spec 2.11); emergencies carry their own badge in the chips. */}
          <span className={`priority-${priorityClass(req.priority)}`}>{priorityLabel(req.priority)}</span>
          <span style={{ fontSize: "0.7rem", padding: "2px 8px", borderRadius: "100px", background: `${deptColor}20`, color: deptColor, border: `1px solid ${deptColor}44` }}>{req.category}</span>
        </div>
        <p style={{ color: "rgba(255,255,255,0.45)", fontSize: "0.88rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{req.description}</p>
        {/* v2: emergency, state, SLA countdown, escalation step, photo and guest
            stage, mood, source and language, then the guest's own words */}
        <div style={{ marginTop: "6px" }}><V2Chips req={req} now={now} withProofAlert /></div>
        <ProofAlert req={req} />
        <OriginalToggle req={req} />

        {/* Assignment & Audit Info */}
        <div style={{ display: "flex", gap: "12px", marginTop: "8px", fontSize: "0.75rem", flexWrap: "wrap", alignItems: "center" }}>
          <PhotoThumb req={req} room={req.room} size={40} onOpen={() => onOpen(req.id)} />
          {req.assigned_to && (
            <span style={{ display: "flex", alignItems: "center", gap: "4px", background: "rgba(225, 175, 104,0.1)", color: "var(--hc-gold)", padding: "3px 8px", borderRadius: "6px", fontWeight: 600 }}>
              <BsPersonCircle /> {req.assigned_to}
            </span>
          )}
          <EtaChip req={req} now={now} />
          {accepted && <span style={{ color: "rgba(255,255,255,0.3)", display: "flex", alignItems: "center", gap: "3px" }}>{accepted.label}: {formatTimestamp(accepted.at, hf.tz, hf.locale)}</span>}
          {req.resolved_at && <span style={{ color: "rgba(255,255,255,0.3)", display: "flex", alignItems: "center", gap: "3px" }}>Resolved: {formatTimestamp(req.resolved_at, hf.tz, hf.locale)}</span>}
        </div>
      </div>

      <div className="req-card-status-info" style={{ textAlign: "right", paddingRight: "20px", flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "8px", flexWrap: "wrap" }}>
        <div style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.28)", display: "flex", alignItems: "center", gap: "4px", justifyContent: "flex-end" }}>
          <BsClockHistory /> {relativeTime(req.timestamp)}
        </div>
        <span className={`status-badge status-${req.status}`}>{req.status === "progress" ? "In Progress" : req.status}</span>
        {/* Time taken (05 spec 2.13): live until a person accepts, then frozen, as on the guest's card. */}
        <TimeTaken req={req} />
        {canSimulate && (
          <button type="button" className="svc-linkbtn" disabled={simulating} onClick={() => onSimulate?.(req.id)}
                  title="Demo hotel only: moves this job's clock back 3 minutes, so the supervisor, duty manager and GM steps run now"
                  style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <LuFastForward size={12} aria-hidden="true" /> {simulating ? "Moving the clock..." : "Simulate 3 min"}
          </button>
        )}
      </div>

      <div className="req-card-actions" style={{ display: "flex", gap: "8px", flexShrink: 0, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end", maxWidth: "420px" }}>
        {/* v2 actions by state. Assigning picks a staff member from a list
            (the old free-text "Your Name" never matched anyone). */}
        {(state === "new" || state === "reopened" || state === "scheduled") && canManage && (
          <StaffAssign key={`assign-${req.id}-${req.version ?? 0}`} staff={staff} department={req.category} busy={busy}
                       hotelSlug={req.state ? hotelSlug : undefined} requestId={req.state ? req.id : undefined}
                       onAssign={sid => onAssign(req.id, sid)} />
        )}
        {state === "assigned" && (
          <button className="btn-complete" style={{ padding: "7px 12px", fontSize: "0.8rem" }} disabled={busy}
                  onClick={() => onAction(req.id, "ack")} title="The assignee has seen it">
            Ack
          </button>
        )}
        {state === "acknowledged" && (
          <button className="btn-complete" style={{ padding: "7px 12px", fontSize: "0.8rem" }} disabled={busy}
                  onClick={() => onAction(req.id, "start")}>
            On the way
          </button>
        )}
        {/* A manager can move the job to someone else until it is done. */}
        {(state === "assigned" || state === "acknowledged" || state === "in_progress") && canManage && (
          <StaffAssign key={`reassign-${req.id}-${req.version ?? 0}`} staff={staff} department={req.category} busy={busy} label="Reassign"
                       hotelSlug={req.state ? hotelSlug : undefined} requestId={req.state ? req.id : undefined}
                       onAssign={sid => onAssign(req.id, sid)} />
        )}
        {(state === "assigned" || state === "acknowledged" || state === "in_progress") && (
          <button className="btn-complete" style={{ background: "var(--hc-success)", color: "#fff", padding: "9px 16px" }} disabled={busy}
                  title={req.needs_proof && req.proof_status !== "verified"
                    ? "No verified photo yet: this closes the job on your word (staff close it with a photo on the staff app)"
                    : undefined}
                  onClick={() => onStatusChange(req.id, "completed")}>
            <BsCheckCircleFill /> Resolve
          </button>
        )}
        {state === "completed" && canManage && req.state && (
          <button className="btn-ghost" style={{ padding: "7px 12px", fontSize: "0.8rem" }} disabled={busy}
                  onClick={() => onAction(req.id, "reopen")}>
            Reopen
          </button>
        )}
        {req.status === "completed" && (
          <button className="btn-complete" style={{ background: "rgba(255,255,255,0.04)", color: "rgba(255,255,255,0.4)", cursor: "default", padding: "9px 16px" }} disabled>
            Resolved
          </button>
        )}
        {canManage && (open || !req.state) && (
          <button
            type="button"
            className="delete-btn"
            style={{ background: "none", border: "none", color: "rgba(255,80,80,0.5)", cursor: "pointer", padding: "9px" }}
            onClick={() => onDelete(req.id)}
            title={req.state ? "Cancel this request (it stays in the history)" : "Delete this request"}
            aria-label={req.state ? `Cancel the request for room ${req.room}` : `Delete the request for room ${req.room}`}
          >
            <BsTrash size={17} />
          </button>
        )}
      </div>
    </div>
  );
}

/* ── Guest Card (interactive, for dashboard) ── */
interface GuestCardProps {
  room: GuestRoom;
  onEdit: (room: GuestRoom) => void;
  onDelete: (rn: string) => void;
  onViewTimeline: () => void;
}

function GuestCard({ room, onEdit, onDelete, onViewTimeline }: GuestCardProps) {
  const hf = useFormat();
  const primary = room.guests?.find(g => g.type === "primary");
  const others = room.guests?.filter(g => g.type !== "primary") || [];
  const prefs = room.preferences || {};
  const tierColor = TIER_COLORS[room.loyaltyTier || "member"] || TIER_COLORS.member;

  return (
    <div className="guest-card">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontFamily: "'Inter',sans-serif", fontSize: 16, fontWeight: 700, color: "var(--hc-gold)" }}>
            Room {room.roomNumber}
          </span>
          {room.stayType && (
            <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 4, background: "rgba(255,255,255,0.05)", color: "rgba(255,255,255,0.5)", textTransform: "capitalize" }}>
              {STAY_ICONS[room.stayType] || "🏨"} {room.stayType}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: "4px" }}>
          <button onClick={() => onViewTimeline()} style={{ background: "none", border: "none", color: "rgba(225, 175, 104,0.8)", cursor: "pointer", padding: "4px" }} title="View Timeline"><BsClockHistory size={15} /></button>
          <button onClick={() => onEdit(room)} style={{ background: "none", border: "none", color: "rgba(255,255,255,0.3)", cursor: "pointer", padding: "4px" }}><LuPencil size={15} /></button>
          <button onClick={() => onDelete(room.roomNumber)} style={{ background: "none", border: "none", color: "rgba(239,68,68,0.4)", cursor: "pointer", padding: "4px" }}><LuTrash2 size={15} /></button>
        </div>
      </div>

      {room.checkIn && room.checkOut && (
        <div style={{ display: "flex", alignItems: "center", fontSize: 12, color: "rgba(255,255,255,0.3)", marginBottom: 10, gap: 5 }}>
          <LuCalendar size={12} />
          {formatDateShort(room.checkIn, hf.tz, hf.locale)} - {formatDateShort(room.checkOut, hf.tz, hf.locale)}
        </div>
      )}

      {primary && (
        <div style={{ fontSize: 15, fontWeight: 600, color: "#fff", marginBottom: 4 }}>
          {primary.name}
          {others.length > 0 && <span style={{ fontWeight: 400, opacity: 0.4, fontSize: "0.85em" }}> + {others.length} guests</span>}
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
        {room.loyaltyTier && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 3, fontSize: 10, padding: "2px 8px", borderRadius: 4, background: `${tierColor}22`, color: tierColor, border: `1px solid ${tierColor}44`, textTransform: "capitalize" }}>
            <LuCrown size={10} /> {room.loyaltyTier}
          </span>
        )}
        {room.occasions?.map((o, i) => (
          <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 3, fontSize: 10, padding: "2px 8px", borderRadius: 4, background: "rgba(239,68,68,0.1)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.25)", textTransform: "capitalize" }}>
            <LuHeart size={10} /> {o}
          </span>
        ))}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
        {prefs.tags && prefs.tags.length > 0 && (
          <div style={{ display: "flex", alignItems: "flex-start", gap: 6, fontSize: 12, color: "rgba(255,255,255,0.5)" }}>
            <LuSparkles size={12} style={{ color: "var(--hc-gold)", flexShrink: 0, marginTop: 2 }} />
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
              {prefs.tags.map((tag, i) => (
                <span key={i} style={{ background: "rgba(255,255,255,0.05)", padding: "1px 6px", borderRadius: 4 }}>{tag}</span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ═══════════════════════ Main Dashboard ════════════════════════════ */
/** "grand-palais-mumbai" -> "Grand Palais Mumbai". The slug is the only name
    the dashboard has until the property record loads. */
function titleiseSlug(slug: string) {
  return slug.split("-").filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export default function StaffDashboard({ hotelSlug }: { hotelSlug: string }) {
  // The slug is only a stand-in until the hotel's own name arrives (a slug
  // like "demo-deira-crest" is not what the property is called).
  const [hotelName, setHotelName] = useState(() => titleiseSlug(hotelSlug));
  useEffect(() => {
    let live = true;
    fetch(`/api/clefs/hotels/${encodeURIComponent(hotelSlug)}/context`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (live && d?.hotel?.name) setHotelName(d.hotel.name); })
      .catch(() => {});
    return () => { live = false; };
  }, [hotelSlug]);
  const hf = useFormat();
  const [requests, setRequests] = useState<Request[]>([]);
  const [query, setQuery] = useState("");
  const [activeTab, setActiveTab] = useState("all");
  const [showModal, setShowModal] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [filters, setFilters] = useState<FilterState>({ status: "all", time: "all", type: "all" });
  const [sidebarView, setSidebarView] = useState<SidebarView>("dashboard");
  const [guestRooms, setGuestRooms] = useState<Record<string, GuestRoom>>({});
  const [showGuestModal, setShowGuestModal] = useState(false);
  const [editingGuestData, setEditingGuestData] = useState<GuestRoom | null>(null);
  const [activeDepartment, setActiveDepartment] = useState("All");
  const [timelineRoom, setTimelineRoom] = useState<string | null>(null);
  const [toastMsg, setToastMsg] = useState<string | null>(null);
  // v2: in-flight transition, a ticking clock for SLA chips, and who may assign.
  const [busyId, setBusyId] = useState<number | null>(null);
  const now = useNow();
  const uiToast = useToast();
  const session = useSession();
  const { can } = session;
  const canManage = can("service.manage");
  // 05 spec: the live link's state, the approvals panel's refresh, the open
  // request detail, and the demo-only clock helper (demo hotels only).
  const hotelCtx = useHotelOptional();
  const isDemo = hotelCtx?.hotel?.status === "demo";
  const guestsModuleOn = hotelCtx ? hotelCtx.moduleEnabled("guests") : true;
  const [streamStatus, setStreamStatus] = useState<StreamStatus | null>(null);
  const [approvalsKey, setApprovalsKey] = useState(0);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [simulatingId, setSimulatingId] = useState<number | null>(null);
  const signedInAs = session.user?.name ?? (session.legacyMode ? "Concierge Desk" : null);
  const signedInRole = session.role ? ROLE_LABELS[session.role as Role] ?? session.role : null;
  const requestsRef = useRef<Request[]>([]);
  const staffListRef = useRef<StaffMember[]>([]);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [showInsights, setShowInsights] = useState(true);
  const filterRef = useRef<HTMLDivElement>(null);

  // Staff state
  const [staffList, setStaffList] = useState<StaffMember[]>([]);
  const [showStaffModal, setShowStaffModal] = useState(false);
  const [editingStaff, setEditingStaff] = useState<StaffMember | null>(null);
  const [staffForm, setStaffForm] = useState({ name: "", role: "", department: "Housekeeping", is_active: true, is_online: false, phone_number: "" });

  // Analytics state
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [expandedStaff, setExpandedStaff] = useState<number | null>(null);


  const playAlertSound = useCallback(() => {
     try {
         const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
         const osc = ctx.createOscillator();
         const gain = ctx.createGain();
         osc.connect(gain);
         gain.connect(ctx.destination);
         osc.type = "sine";
         osc.frequency.setValueAtTime(880, ctx.currentTime);
         osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.1);
         gain.gain.setValueAtTime(0, ctx.currentTime);
         gain.gain.linearRampToValueAtTime(0.5, ctx.currentTime + 0.05);
         gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.5);
         osc.start(ctx.currentTime);
         osc.stop(ctx.currentTime + 0.5);
     } catch(e) {}
  }, []);
  /* ── Load from API on mount ── */
  useEffect(() => {
    Promise.all([fetchRequests(hotelSlug), fetchGuestRooms(hotelSlug), fetchStaff(hotelSlug)]).then(
      ([reqs, guests, staff]) => {
        setRequests(reqs.map(cleanCategory));
        setGuestRooms(guests);
        setStaffList(staff);
      }
    );
  }, [hotelSlug]);

  // Transitions read the version a card was drawn from without re-binding.
  useEffect(() => { requestsRef.current = requests; }, [requests]);
  useEffect(() => { staffListRef.current = staffList; }, [staffList]);

  /* ── SSE: real-time push from server ──
     openHotelStream mints a short-lived ticket (legacy open-access hotels fall
     back to the ticketless URL) and reconnects with backoff. While the stream
     is down it runs the v2 cursor poll every 15 s: GET /service/events after
     the last seen event, and a full refresh only when something happened. */
  useEffect(() => {
    const refreshRequests = () => {
      fetchRequests(hotelSlug).then(reqs => setRequests(reqs.map(cleanCategory)));
    };
    const cursor = { at: null as number | null, dead: false };
    const pollCursor = async () => {
      if (cursor.dead) { refreshRequests(); return; }
      try {
        // The first poll refreshes the list and starts at the head of the
        // feed (cursor=latest), so it never walks the hotel's whole history.
        // After that, a reconnect walks at most 4 pages per tick.
        if (cursor.at === null) {
          const head = await apiFetch<{ events: unknown[]; cursor: number }>(
            `/hotels/${hotelSlug}/service/events`, { query: { cursor: "latest" } });
          cursor.at = head.cursor;
          refreshRequests();
          return;
        }
        let changed = false;
        for (let page = 0; page < 4; page++) {
          const after: number = cursor.at ?? 0;
          const out: { events: unknown[]; cursor: number } = await apiFetch(
            `/hotels/${hotelSlug}/service/events`, { query: { cursor: after, limit: 500 } });
          if (out.events.length) changed = true;
          cursor.at = out.cursor;
          if (out.events.length < 500) break;
        }
        if (changed) refreshRequests();
      } catch {
        // No v2 feed on this server (or no permission): poll the list itself.
        cursor.dead = true;
        refreshRequests();
      }
    };

    // The approvals panel reloads on recovery changes, and on request changes
    // too ("not fixed" reopens the job as it opens the case).
    const refreshApprovals = () => setApprovalsKey(k => k + 1);
    // The header's Live / Reconnecting pill (05 spec: a live dashboard).
    const status = graceStatus<StreamStatus>(setStreamStatus);

    const close = openHotelStream(hotelSlug, {
      [ASSISTANT_REQUEST_EVENT]: (newReq: any) => {
        try {
          // Unparseable payloads arrive as raw strings; ignore them as before.
          if (!newReq || typeof newReq !== "object") return;
          playAlertSound();
          const label = newReq.type === "Food" || newReq.category === "Food & Beverage" ? "Food Order" : "AI Request";
          setToastMsg(`New ${label} from Room ${newReq.room}`);
          setTimeout(() => setToastMsg(null), 5000);
        } catch (err) {
          console.error("Error handling new assistant request", err);
        }
      },
      requests_changed: () => { refreshRequests(); refreshApprovals(); },
      "service.recovery_changed": refreshApprovals,
      // Proof before closure (05 spec 2.8): a rejected photo keeps the job
      // open and alerts the supervisor; the desk hears it too.
      "service.proof_rejected": (d: any) => {
        if (!d || typeof d !== "object") return;
        refreshRequests();
        const where = `room ${d.room ?? "-"}`;
        if (d.status === "rejected") uiToast.error(`Photo rejected: ${where}`, "The job stays open and the supervisor was alerted.");
        else uiToast.info(`Photo needs a look: ${where}`, "The automatic check could not run, so the supervisor was asked to look.");
      },
    }, {
      refetch: () => { void pollCursor(); refreshApprovals(); },
      pollMs: 15_000,
      onStatus: status.onStatus,
    });
    return () => { status.cancel(); close(); };
  }, [hotelSlug, playAlertSound, uiToast]);

  /* ── Close filter panel when clicking outside ── */
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) {
        setShowFilters(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  /* ── Status change: v2 transitions (POST /service/requests/{id}/transition) ──
     Every step names the version the card was drawn from, so two people
     tapping at once get "already done by X" instead of a silent overwrite. */
  const reloadRequests = useCallback(() => {
    fetchRequests(hotelSlug).then(reqs => setRequests(reqs.map(cleanCategory)));
  }, [hotelSlug]);

  const runTransition = useCallback(async (id: number, action: TransitionAction, staffId?: number) => {
    const current = requestsRef.current.find(r => r.id === id);
    setBusyId(id);
    try {
      const out = await transition(hotelSlug, id, {
        action, staff_id: staffId, expected_version: current?.version ?? undefined,
      });
      const next = cleanCategory(out.request as unknown as Request);
      setRequests(prev => prev.map(r => (r.id === id ? { ...r, ...next } : r)));
      return true;
    } catch (err) {
      // Legacy open-access visitors may only use the legacy PATCH.
      if (takesLegacyPath(err) && current) {
        const ok = await legacyPatch(hotelSlug, current, action, staffListRef.current.find(s => s.id === staffId)?.name);
        if (ok) { reloadRequests(); return true; }
        uiToast.error("That step needs a signed-in account.");
        return false;
      }
      const text = conflictText(err);
      if (text) {
        uiToast.info(text);
        reloadRequests();
      } else if (err instanceof ApiError && err.status === 403) {
        uiToast.error("Only a manager can do that.");
      } else {
        uiToast.error("Could not update the request", errorText(err));
      }
      return false;
    } finally {
      setBusyId(null);
    }
  }, [hotelSlug, reloadRequests, uiToast]);

  const handleAssign = useCallback(async (id: number, staffId: number) => {
    const current = requestsRef.current.find(r => r.id === id);
    const st = current ? effectiveState(current) : "new";
    await runTransition(id, st === "assigned" || st === "acknowledged" || st === "in_progress" ? "reassign" : "assign", staffId);
  }, [runTransition]);

  const handleAction = useCallback((id: number, action: TransitionAction) => {
    void runTransition(id, action);
  }, [runTransition]);

  const handleStatusChange = useCallback(async (id: number, newStatus: "new" | "progress" | "completed") => {
    if (newStatus === "completed") await runTransition(id, "complete");
    else if (newStatus === "new") await runTransition(id, "unassign");
    else await runTransition(id, "ack");
  }, [runTransition]);

  /* ── Delete ── */
  const deleteRequest = useCallback(async (id: number) => {
    await fetch(`${API_BASE}/hotels/${hotelSlug}/requests/${id}`, { method: "DELETE" });
    setRequests(prev => prev.filter(r => r.id !== id));
  }, [hotelSlug]);

  /* The trash icon cancels a v2 request (it stays in the history, the
     analytics and the proof trail); only a pre-v2 row is still deleted.
     Either way it asks first: one stray tap used to erase a request. */
  const removeRequest = useCallback(async (id: number) => {
    const current = requestsRef.current.find(r => r.id === id);
    if (!current) return;
    if (current.state) {
      if (!window.confirm(`Cancel the request for room ${current.room}? It stays in the service history.`)) return;
      await runTransition(id, "cancel");
      return;
    }
    if (!window.confirm(`Delete the request for room ${current.room}? This cannot be undone.`)) return;
    await deleteRequest(id);
  }, [runTransition, deleteRequest]);

  /* ── Demo helper (05 spec 3: demo hotels only): move a job's clock back
     3 minutes so the supervisor, duty manager and GM steps run live. ── */
  const simulateMinutes = useCallback(async (id: number) => {
    setSimulatingId(id);
    try {
      const card = await demoAdvance(hotelSlug, id, 3);
      if (card) {
        const next = cleanCategory(card as unknown as Request);
        setRequests(prev => prev.map(r => (r.id === id ? { ...r, ...next } : r)));
      }
      reloadRequests();
      const step = card ? escalationLabel(card.escalation_level) : null;
      uiToast.info("Moved this job's clock 3 minutes", step ? `Escalated: ${step}` : "The escalation ladder now runs on the new time.");
    } catch (err) {
      if (isMissingRoute(err)) uiToast.error("Simulating time is not available on this server yet.");
      else if (err instanceof ApiError && (err.status === 403 || err.status === 404)) uiToast.error("Only a manager of a demo hotel can simulate time.", errorText(err));
      else uiToast.error("Could not move the clock", errorText(err));
    } finally {
      setSimulatingId(null);
    }
  }, [hotelSlug, reloadRequests, uiToast]);

  /* ── Staff CRUD ── */
  const openAddStaff = () => {
    setEditingStaff(null);
    setStaffForm({ name: "", role: "", department: "Housekeeping", is_active: true, is_online: false, phone_number: "" });
    setShowStaffModal(true);
  };
  const openEditStaff = (s: StaffMember) => {
    setEditingStaff(s);
    setStaffForm({ name: s.name, role: s.role, department: s.department, is_active: s.is_active, is_online: s.is_online, phone_number: s.phone_number || "" });
    setShowStaffModal(true);
  };
  const saveStaff = async () => {
    if (!staffForm.name.trim() || !staffForm.role.trim()) return;
    if (editingStaff) {
      const res = await fetch(`${API_BASE}/hotels/${hotelSlug}/staff/${editingStaff.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(staffForm),
      });
      if (res.ok) { const updated: StaffMember = await res.json(); setStaffList(prev => prev.map(s => s.id === updated.id ? updated : s)); }
    } else {
      const res = await fetch(`${API_BASE}/hotels/${hotelSlug}/staff`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(staffForm),
      });
      if (res.ok) { const created: StaffMember = await res.json(); setStaffList(prev => [...prev, created]); }
    }
    setShowStaffModal(false);
  };
  const deleteStaff = async (id: number) => {
    if (!confirm("Remove this staff member?")) return;
    await fetch(`${API_BASE}/hotels/${hotelSlug}/staff/${id}`, { method: "DELETE" });
    setStaffList(prev => prev.filter(s => s.id !== id));
  };
  const seedStaff = async () => {
    const res = await fetch(`${API_BASE}/hotels/${hotelSlug}/staff/seed`, { method: "POST" });
    if (res.ok) { const seeded: StaffMember[] = await res.json(); setStaffList(seeded); setToastMsg("Demo staff loaded!"); setTimeout(() => setToastMsg(null), 3000); }
  };

  /* ── Manual Entry submit ── */
  const handleManualSubmit = async (data: Omit<Request, "id" | "timestamp" | "status" | "category">) => {
    // v2 intake with source phone_desk (the desk typing up a call), so the
    // request is routed, timed and counted as a phoned-in job.
    const subtype = data.subtype?.trim();
    const description = subtype && !data.description.includes(subtype) ? `${subtype}: ${data.description}` : data.description;
    try {
      const out = await createRequest(hotelSlug, {
        room: data.room, type: data.type, description, priority: data.priority,
        category: CATEGORIES[data.type] ?? "General", source: "phone_desk",
      });
      const newReq = cleanCategory(out.request as unknown as Request);
      setRequests(prev => [newReq, ...prev.filter(r => r.id !== newReq.id)]);
      if (out.merged) uiToast.info(`Added to the open request for room ${newReq.room}.`);
      return;
    } catch (err) {
      if (!takesLegacyPath(err)) {
        uiToast.error("Could not file the request", errorText(err));
        return;
      }
    }
    const res = await fetch(`${API_BASE}/hotels/${hotelSlug}/requests`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...data,
        timestamp: Date.now(),
        status: "new",
        category: CATEGORIES[data.type] ?? "General",
      }),
    });
    if (res.ok) {
      const newReq: Request = await res.json();
      setRequests(prev => [newReq, ...prev]);
    }
  };

  /* ── Phone entry (03 spec 1.8): the "N" hotkey and the New request button
     open PhoneEntryConsole in a drawer. The old ManualEntryModal stays one
     release behind NEXT_PUBLIC_SERVICE_CLASSIC_ENTRY=1. ── */
  const [showPhoneEntry, setShowPhoneEntry] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "n" && e.key !== "N") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
      if (document.querySelector(".cx-overlay")) return; // another dialog is open
      e.preventDefault();
      if (CLASSIC_ENTRY) setShowModal(true);
      else setShowPhoneEntry(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  /* ── Time filter helper ── */
  const matchesTime = (ts: number) => {
    const diff = Date.now() - ts;
    if (filters.time === "15min") return diff <= 15 * 60_000;
    if (filters.time === "1hour") return diff <= 60 * 60_000;
    if (filters.time === "today") {
      // The hotel's midnight, not the browser's (03 spec 1.8).
      return ts >= hotelDayStart(wallClock(), hf.tz);
    }
    return true;
  };

  /* ── Combined filtering ── */
  // Safety first (05 spec): an open emergency is never hidden by a
  // department chip (it is filed under Security, which has no chip) and it
  // sits at the top of the list.
  const isOpenEmergency = (req: Request) => isOpenRow(req) && isEmergency(req);
  const matching = requests.filter(req => {
    // Tab (status quick-filter)
    if (activeTab !== "all" && req.status !== activeTab) return false;
    // Department filtering component integration
    if (activeDepartment !== "All" && req.category !== activeDepartment && !isOpenEmergency(req)) return false;
    // Advanced status filter (filter panel overrides tab if set)
    if (filters.status !== "all" && req.status !== filters.status) return false;
    // Time
    if (!matchesTime(req.timestamp)) return false;
    // Type
    if (filters.type !== "all" && req.type !== filters.type) return false;
    // Search query
    if (query.trim()) {
      const q = query.toLowerCase();
      return (
        req.room.toLowerCase().includes(q) ||
        req.type.toLowerCase().includes(q) ||
        (req.subtype || "").toLowerCase().includes(q) ||
        req.status.toLowerCase().includes(q) ||
        req.category.toLowerCase().includes(q) ||
        req.description.toLowerCase().includes(q)
      );
    }
    return true;
  });
  const filtered = [...matching.filter(isOpenEmergency), ...matching.filter(r => !isOpenEmergency(r))];
  const openEmergencies = requests.filter(isOpenEmergency);
  const detailRequest = detailId === null ? null : requests.find(r => r.id === detailId) ?? null;
  // The approvals panel reads the guest's mood from the request a case is about.
  const moodOf = useCallback((id: number) => {
    const r = requests.find(x => x.id === id);
    return r ? { mood: r.mood, frustration: r.frustration } : null;
  }, [requests]);

  /* ── Stats ── */
  const stats = {
    all: requests.length,
    new: requests.filter(r => r.status === "new").length,
    progress: requests.filter(r => r.status === "progress").length,
    completed: requests.filter(r => r.status === "completed").length,
  };

  /* ── Chart data ──────────────────────────────────────────────────────
     Derived from the request history the dashboard already holds, so every
     chart moves with the SSE stream instead of needing its own endpoint.
     "Today" here means the property's local midnight, which is the day a GM
     actually reads. ------------------------------------------------------ */
  const insights = useMemo(() => {
    // Hotel-local midnight and hours (hf.tz), not the browser's zone.
    const t0 = hotelDayStart(wallClock(), hf.tz);
    const statusIdx = (st: string) => (st === "new" ? 0 : st === "progress" ? 1 : 2);
    const hourTick = (h: number) => (h === 0 ? "12a" : h === 12 ? "12p" : h < 12 ? `${h}a` : `${h - 12}p`);

    const today = requests.filter(r => r.timestamp >= t0);

    /* Volume by hour - the staffing question: when does the property get busy? */
    const hourly: ColumnDatum[] = Array.from({ length: 24 }, (_, h) => ({
      label: `${hourTick(h)} – ${hourTick((h + 1) % 24)}`,
      tick: h % 3 === 0 ? hourTick(h) : undefined,
      values: [0],
    }));
    today.forEach(r => {
      const h = hotelHour(r.timestamp, hf.tz);
      if (hourly[h]) hourly[h].values[0] += 1;
    });
    const currentHour = hotelHour(wallClock(), hf.tz);
    hourly[currentHour].tick = "now";           // marks the live hour without recolouring the bar
    const peakHour = hourly.reduce((best, d, i) => (d.values[0] > hourly[best].values[0] ? i : best), 0);
    const peakLabel = hourly[peakHour].label;

    /* Today's load per department, split by where each request stands */
    const deptRows: BarRow[] = DEPARTMENTS.map(d => ({
      label: d === "Food & Beverage" ? "In-Room Dining" : d,
      values: [0, 0, 0],
    }));
    const deptPos = new Map(DEPARTMENTS.map((d, i) => [d, i]));
    today.forEach(r => {
      const i = deptPos.get(r.category) ?? DEPARTMENTS.length - 1;
      deptRows[i].values[statusIdx(r.status)] += 1;
    });

    /* Seven-day volume, in the same colour language as the department bars */
    const daily: ColumnDatum[] = [];
    for (let k = 6; k >= 0; k--) {
      // Day edges from the hotel calendar, so a DST day is 23 or 25 hours.
      const from = hotelDayStart(t0, hf.tz, -k);
      const to = hotelDayStart(t0, hf.tz, -k + 1);
      const values = [0, 0, 0];
      requests.forEach(r => {
        if (r.timestamp >= from && r.timestamp < to) values[statusIdx(r.status)] += 1;
      });
      const noon = from + 12 * 3_600_000;
      daily.push({
        label: new Date(noon).toLocaleDateString(hf.locale, { weekday: "long", month: "short", day: "numeric", timeZone: hf.tz }),
        tick: k === 0 ? "Today" : new Date(noon).toLocaleDateString(hf.locale, { weekday: "short", timeZone: hf.tz }),
        values,
      });
    }

    /* How long resolved requests actually took. Bucket edges follow the
       thresholds the rest of this view already uses (10 min fast, 30 slow). */
    const BUCKETS = [
      { tick: "<5",    label: "Under 5 minutes",   hi: 5 },
      { tick: "5–10",  label: "5 to 10 minutes",   hi: 10 },
      { tick: "10–20", label: "10 to 20 minutes",  hi: 20 },
      { tick: "20–30", label: "20 to 30 minutes",  hi: 30 },
      { tick: "30–60", label: "30 to 60 minutes",  hi: 60 },
      { tick: "60m+",  label: "Over an hour",      hi: Infinity },
    ];
    const dist: ColumnDatum[] = BUCKETS.map(b => ({ label: b.label, tick: b.tick, values: [0] }));
    const times: number[] = [];
    requests.forEach(r => {
      if (r.status !== "completed" || !r.accepted_at || !r.resolved_at) return;
      const mins = (r.resolved_at - r.accepted_at) / 60_000;
      if (!Number.isFinite(mins)) return;
      times.push(mins);
      const bi = BUCKETS.findIndex(b => mins < b.hi);
      dist[bi === -1 ? BUCKETS.length - 1 : bi].values[0] += 1;
    });
    times.sort((a, b) => a - b);
    const medianMins = times.length ? times[Math.floor(times.length / 2)] : undefined;
    const withinTarget = times.length
      ? Math.round((times.filter(m => m < 10).length / times.length) * 100)
      : null;

    /* Service mix - the tail folds into "Other" rather than growing more bars */
    const typeCount = new Map<string, number>();
    requests.forEach(r => typeCount.set(r.type, (typeCount.get(r.type) || 0) + 1));
    const ranked = [...typeCount.entries()].sort((a, b) => b[1] - a[1]);
    const mixRows: BarRow[] = ranked.slice(0, 6).map(([t, n]) => ({ label: t, values: [n] }));
    const tail = ranked.slice(6).reduce((acc, [, n]) => acc + n, 0);
    if (tail > 0) mixRows.push({ label: "Other", values: [tail] });

    return {
      hourly, currentHour, peakLabel, peakCount: hourly[peakHour].values[0],
      deptRows, daily, dist, medianMins, withinTarget,
      resolvedCount: times.length, mixRows, todayCount: today.length,
    };
  }, [requests, hf.tz, hf.locale]);

  const guestRoomList = Object.values(guestRooms).sort((a, b) => Number(a.roomNumber) - Number(b.roomNumber));

  const filtersActive = filters.status !== "all" || filters.time !== "all" || filters.type !== "all";

  /* ── History-filtered requests (for service history view) ── */
  const [historyFilter, setHistoryFilter] = useState("all");
  const historyRequests = requests
    .filter(r => historyFilter === "all" || r.status === historyFilter)
    .sort((a, b) => b.timestamp - a.timestamp);

  return (
    <div className="app-container">
      <style dangerouslySetInnerHTML={{ __html: injectStyles }} />

      {/* ── Toast Notification ── */}
      {toastMsg && (
        <div style={{
          position: "fixed", top: "20px", right: "20px", zIndex: 9999,
          background: "var(--hc-success)", color: "#fff", padding: "14px 20px", borderRadius: "12px",
          boxShadow: "0 10px 30px rgba(0,0,0,0.3)", display: "flex", alignItems: "center", gap: "10px",
          animation: "fadeDown 0.3s ease-out forwards", fontWeight: 600
        }}>
          <BsBellFill /> {toastMsg}
        </div>
      )}

      {/* ── Sidebar Overlay (for mobile drawer) ── */}
      {isSidebarOpen && (
        <div 
          className="sidebar-overlay"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      {/* ── Mobile Top Bar ── */}
      <div className="mobile-top-bar">
        <button className="menu-toggle-btn" onClick={() => setIsSidebarOpen(true)}>
          <LuMenu size={24} />
        </button>
        <div className="sd-brand sd-brand--sm">
          <img src="/brand/clefs-key.webp" alt="Clefs" className="sd-brand__mark" />
          <span className="sd-brand__name">{hotelName}</span>
        </div>
        <div style={{ width: 24 }} />
      </div>

      {/* ── Sidebar ── */}
      <aside className={`sidebar-aside ${isSidebarOpen ? "open" : ""}`}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", width: "100%" }}>
          <div className="sd-brand">
            <img src="/brand/clefs-key.webp" alt="Clefs" className="sd-brand__mark" />
            <h2 className="sd-brand__name">{hotelName}</h2>
          </div>
          <button 
            className="menu-close-btn"
            onClick={() => setIsSidebarOpen(false)}
            style={{ background: "none", border: "none", color: "#fff", cursor: "pointer", display: "none" }}
          >
            <LuXClose size={20} />
          </button>
        </div>

        <nav style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <div
            className={`sidebar-item ${sidebarView === "dashboard" ? "active" : ""}`}
            onClick={() => { setSidebarView("dashboard"); setIsSidebarOpen(false); }}
          >
            <BsHouseDoor /> Dashboard
          </div>
          {/* The Guests module can be switched off (CLEFS_DISABLED_MODULES or the
              hotel's module settings); its list then leaves the sidebar too. */}
          {guestsModuleOn && (
            <div
              className={`sidebar-item ${sidebarView === "guests" ? "active" : ""}`}
              onClick={() => { setSidebarView("guests"); fetchGuestRooms(hotelSlug).then(setGuestRooms); setIsSidebarOpen(false); }}
            >
              <BsPersonCircle /> Guest List
            </div>
          )}
          <div
            className={`sidebar-item ${sidebarView === "history" ? "active" : ""}`}
            onClick={() => { setSidebarView("history"); setIsSidebarOpen(false); }}
          >
            <BsClockHistory /> Service History
          </div>
          <div
            className={`sidebar-item ${sidebarView === "staff" ? "active" : ""}`}
            onClick={() => { setSidebarView("staff"); fetchStaff(hotelSlug).then(setStaffList); setIsSidebarOpen(false); }}
          >
            <LuUsers /> Staff Registry
          </div>
          <div
            className={`sidebar-item ${sidebarView === "analytics" ? "active" : ""}`}
            onClick={async () => {
              setSidebarView("analytics");
              setIsSidebarOpen(false);
              setAnalyticsLoading(true);
              const data = await fetchAnalytics(hotelSlug);
              setAnalytics(data);
              setAnalyticsLoading(false);
            }}
          >
            <LuTrendingUp /> Analytics
          </div>
        </nav>

        {/* Service Control v2 pages (the Service sub-nav grows here until extraction). */}
        <nav aria-label="Service tools" style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "12px" }}>
          <Link className="sidebar-item svc-navlink" href={`/app/${encodeURIComponent(hotelSlug)}/service/new`}><BsPlusLg /> Phone entry</Link>
          <Link className="sidebar-item svc-navlink" href={`/app/${encodeURIComponent(hotelSlug)}/service/roster`}><LuCalendar /> Roster</Link>
          <Link className="sidebar-item svc-navlink" href={`/app/${encodeURIComponent(hotelSlug)}/service/analytics`}><LuTrendingUp /> Response times</Link>
          {canManage && (
            <Link className="sidebar-item svc-navlink" href={`/app/${encodeURIComponent(hotelSlug)}/service/config`}><LuPencil /> Setup</Link>
          )}
        </nav>

        <div style={{ marginTop: "auto" }}>
          <div className="glass-card" style={{ padding: "16px", fontSize: "0.84rem", color: "rgba(255,255,255,0.5)" }}>
            Logged in as
            <div style={{ color: "#fff", fontWeight: "600", marginTop: "4px", overflowWrap: "anywhere" }}>
              {signedInAs ?? (session.loading ? "..." : "Not signed in")}
            </div>
            {signedInRole && session.user && (
              <div style={{ fontSize: "0.76rem", marginTop: "2px", color: "rgba(255,255,255,0.4)" }}>{signedInRole}</div>
            )}
          </div>
        </div>
      </aside>

      {/* ── Main ── */}
      <main className="main-content">

        {/* ════════════ DASHBOARD VIEW ════════════ */}
        {sidebarView === "dashboard" && (
          <>
            {/* Header */}
            <header className="dashboard-header">
              <div>
                <h1 style={{ fontSize: "clamp(1.9rem, 3vw, 2.6rem)", marginBottom: "10px" }}>Staff Requests</h1>
                <p style={{ color: "var(--clefs-ink-muted)", display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
                  Guest requests and services, live.
                  <LivePill status={streamStatus} />
                </p>
              </div>

              <div className="header-controls">
                {/* Search */}
                <div className="glass-card" style={{ padding: "10px 18px", display: "flex", alignItems: "center", gap: "10px", borderRadius: "14px" }}>
                  <BsSearch style={{ color: "rgba(255,255,255,0.35)", flexShrink: 0 }} />
                  <input
                    type="text"
                    className="search-input"
                    placeholder="Search room, type, status…"
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                  />
                  {query && (
                    <button onClick={() => setQuery("")} style={{ background: "none", border: "none", color: "rgba(255,255,255,0.3)", cursor: "pointer", display: "flex", padding: 0 }}>
                      <BsX size={18} />
                    </button>
                  )}
                </div>

                {/* Manual Entry */}
                <button className="btn-complete" title="New request (N)"
                        onClick={() => (CLASSIC_ENTRY ? setShowModal(true) : setShowPhoneEntry(true))}>
                  <BsPlusLg /> New request
                </button>
              </div>
            </header>

            {/* Safety first (05 spec): every open emergency, above everything else. */}
            {openEmergencies.length > 0 && (
              <section className="svc-emerg" aria-label="Open emergencies">
                {openEmergencies.map(r => (
                  <div key={r.id} className="svc-emerg__row" role="alert">
                    <LuSiren aria-hidden="true" />
                    <span className="svc-emerg__title">EMERGENCY · Room {r.room || "-"}</span>
                    <span className="svc-emerg__text" dir="auto">
                      {r.description} · {dutyManagerPaged(r) ? "Duty manager paged" : "Paging the duty manager"} · {relativeTime(r.timestamp)}
                    </span>
                    <button type="button" className="btn-ghost" style={{ padding: "6px 12px", fontSize: "0.8rem" }}
                            onClick={() => setDetailId(r.id)}>
                      Open
                    </button>
                  </div>
                ))}
              </section>
            )}

            {/* 05 spec 2.12: recovery gestures for the duty manager to approve. */}
            <ApprovalsPanel hotelSlug={hotelSlug} canManage={canManage} refreshKey={approvalsKey} moodOf={moodOf} />

            {/* Stats Grid */}
            <section className="stats-grid">
              {[
                { label: "Total Tasks", value: stats.all, color: "#fff", sub: "Since login" },
                { label: "New Requests", value: stats.new, color: "var(--hc-gold)", sub: "Priority attention" },
                { label: "In Progress", value: stats.progress, color: "var(--hc-blue)", sub: "Active staff" },
                { label: "Completed", value: stats.completed, color: "var(--hc-success)", sub: "Well done!" },
              ].map((s, i) => (
                <div key={i} className="glass-card sd-stat" style={{ padding: "24px" }}>
                  <div className="sd-stat__label">{s.label}</div>
                  <div className="sd-stat__value" style={{ color: s.color }}>{s.value}</div>
                  <div className="sd-stat__sub">{s.sub}</div>
                </div>
              ))}
            </section>

            {/* ════════ Today at a glance ════════
                Two questions a duty manager asks on sight: when is the rush,
                and which department is carrying it. Collapsible, because the
                request list below is what a shift actually works from. */}
            <section style={{ marginBottom: showInsights ? "40px" : "28px" }}>
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "16px", marginBottom: "14px", flexWrap: "wrap" }}>
                <div style={{ fontSize: "0.72rem", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--clefs-ink-faint)" }}>
                  Today at a glance
                </div>
                <button className="insight-toggle" onClick={() => setShowInsights(v => !v)}>
                  {showInsights ? <>Hide <LuChevronUp size={13} /></> : <>Show <LuChevronDown size={13} /></>}
                </button>
              </div>

              {showInsights && (
                <div className="insight-grid">
                  <ChartCard
                    title="Request flow"
                    hint={
                      insights.todayCount === 0
                        ? "Fills in through the day as requests arrive."
                        : `${insights.todayCount} request${insights.todayCount === 1 ? "" : "s"} since midnight · busiest ${insights.peakLabel}.`
                    }
                  >
                    <ColumnChart
                      data={insights.hourly}
                      series={[{ label: "Requests", color: CHART_NEW }]}
                      height={172}
                      highlightIndex={insights.currentHour}
                      ariaLabel="Guest requests received per hour of the day so far"
                      emptyNote="No requests since midnight. The hours fill in as the day runs."
                    />
                  </ChartCard>

                  <ChartCard
                    title="Where the pressure is"
                    hint="Today's requests by department, split by where each one stands."
                    legend={STATUS_SERIES}
                  >
                    <HBarChart
                      rows={insights.deptRows}
                      series={STATUS_SERIES}
                      ariaLabel="Requests today grouped by department and status"
                      emptyNote="Department load appears with today's first request."
                    />
                  </ChartCard>
                </div>
              )}
            </section>

            {/* Department Tabs */}
            <div className="dept-tabs-container">
               {["All", "Housekeeping", "Food & Beverage", "Front Desk", "Maintenance", "Concierge", "General"].map(dept => (
                 <button key={dept} onClick={() => setActiveDepartment(dept)} className={`btn-ghost ${activeDepartment === dept ? "active" : ""}`} style={{ padding: "8px 16px", borderRadius: "100px", fontSize: "0.85rem", whiteSpace: "nowrap" }}>
                   {dept === "Food & Beverage" ? "In-Room Dining" : dept}
                 </button>
               ))}
            </div>

            {/* Tab row (Status) */}
            <div className="status-tabs-container">
              {(["all", "new", "progress", "completed"] as const).map(tab => (
                <div
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className="status-tab"
                  style={{
                    cursor: "pointer",
                    color: activeTab === tab ? "var(--hc-gold)" : "rgba(255,255,255,0.4)",
                    fontWeight: "500", fontSize: "0.92rem",
                    textTransform: "capitalize", position: "relative"
                  }}
                >
                  {tab === "progress" ? "In Progress" : tab}
                  {activeTab === tab && <div className="tab-underline" />}
                </div>
              ))}
            </div>

            {/* Search / Filter summary */}
            {(query || filtersActive) && (
              <div style={{ marginBottom: "18px", display: "flex", alignItems: "center", gap: "10px", color: "rgba(255,255,255,0.4)", fontSize: "0.85rem" }}>
                <BsExclamationCircle size={13} />
                Showing <strong style={{ color: "#fff" }}>{filtered.length}</strong> of {requests.length} requests
                {query && <span>· Searching <em>&quot;{query}&quot;</em></span>}
              </div>
            )}

            {/* Request list */}
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              {filtered.length > 0 ? filtered.map(req => (
                <RequestCard key={req.id} hotelSlug={hotelSlug} req={req} onStatusChange={handleStatusChange} onDelete={removeRequest} onAssign={handleAssign}
                             onAction={handleAction} onOpen={setDetailId}
                             onSimulate={isDemo && canManage ? simulateMinutes : undefined} simulating={simulatingId === req.id}
                             staff={staffList} now={now} busy={busyId === req.id} canManage={canManage} />
              )) : (
                <div className="glass-card" style={{ padding: "80px", textAlign: "center", color: "rgba(255,255,255,0.2)" }}>
                  <div style={{ fontSize: "3rem", marginBottom: "16px" }}><BsBellFill /></div>
                  <p style={{ fontSize: "1rem" }}>No requests match your filters</p>
                  <p style={{ fontSize: "0.85rem", marginTop: "8px", color: "rgba(255,255,255,0.12)" }}>Try a different search or filter</p>
                </div>
              )}
            </div>
          </>
        )}

        {/* ════════════ GUEST LIST VIEW ════════════ */}
        {sidebarView === "guests" && (
          <>
            <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "40px", flexWrap: "wrap", gap: "20px" }}>
              <div>
                <h1 style={{ fontSize: "2.4rem", marginBottom: "8px" }}>Guest List</h1>
                <p style={{ color: "rgba(255,255,255,0.4)" }}>Guests staying now, and what they prefer.</p>
              </div>
              <div style={{ display: "flex", gap: "12px" }}>
                <button
                  onClick={() => { setEditingGuestData(null); setShowGuestModal(true); }}
                  className="btn-complete"
                >
                  <BsPlusLg size={13} /> Add Room
                </button>
              </div>
            </header>

            {guestRoomList.length === 0 ? (
              <div className="glass-card" style={{ padding: "80px", textAlign: "center", color: "rgba(255,255,255,0.2)" }}>
                <div style={{ fontSize: "3rem", marginBottom: "16px" }}><LuUsers /></div>
                {can("guests.view") ? (
                  <>
                    <p style={{ fontSize: "1rem" }}>No guest profiles found</p>
                    <p style={{ fontSize: "0.85rem", marginTop: "8px", color: "rgba(255,255,255,0.12)" }}>Add a room profile to get started.</p>
                  </>
                ) : (
                  <>
                    <p style={{ fontSize: "1rem" }}>Guest profiles are not open to your role</p>
                    <p style={{ fontSize: "0.85rem", marginTop: "8px", color: "rgba(255,255,255,0.3)" }}>Ask the owner or GM if you need them.</p>
                  </>
                )}
              </div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(320px, 100%), 1fr))", gap: 18 }}>
                {guestRoomList.map(room => (
                  <GuestCard
                    key={room.roomNumber}
                    room={room}
                    onEdit={(data) => { setEditingGuestData(data); setShowGuestModal(true); }}
                    onViewTimeline={() => { setTimelineRoom(room.roomNumber); setSidebarView("room-timeline"); }}
                    onDelete={async (rn) => {
                      if (confirm(`Delete profile for Room ${rn}?`)) {
                        await fetch(`${API_BASE}/hotels/${hotelSlug}/guests/${rn}`, { method: "DELETE" });
                        setGuestRooms(await fetchGuestRooms(hotelSlug));
                      }
                    }}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {/* ════════════ SERVICE HISTORY VIEW ════════════ */}
        {sidebarView === "history" && (
          <>
            <header className="view-header">
              <div>
                <h1>Service History</h1>
                <p style={{ color: "rgba(255,255,255,0.4)" }}>The last 7 days, and every request still open.</p>
              </div>
              <div className="history-filters">
                <span style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.4)" }}>Show:</span>
                {(["all", "new", "progress", "completed"] as const).map(f => (
                  <button
                    key={f}
                    onClick={() => setHistoryFilter(f)}
                    className={`btn-ghost ${historyFilter === f ? "active" : ""}`}
                    style={{ padding: "7px 14px", fontSize: "0.82rem" }}
                  >
                    {f === "progress" ? "In Progress" : f === "all" ? "All" : f.charAt(0).toUpperCase() + f.slice(1)}
                  </button>
                ))}
              </div>
            </header>

            <div style={{ fontSize: "0.85rem", color: "rgba(255,255,255,0.35)", marginBottom: 20 }}>
              Showing <strong style={{ color: "#fff" }}>{historyRequests.length}</strong> requests
            </div>

            {historyRequests.length === 0 ? (
              <div className="glass-card" style={{ padding: "80px", textAlign: "center", color: "rgba(255,255,255,0.2)" }}>
                <div style={{ fontSize: "3rem", marginBottom: "16px" }}><BsClockHistory /></div>
                <p style={{ fontSize: "1rem" }}>No service history yet</p>
              </div>
            ) : (
              <div className="glass-card" style={{ overflow: "hidden", borderRadius: 16 }}>
                <div style={{ overflowX: "auto", width: "100%" }}>
                  <table className="history-table">
                    <thead>
                      <tr>
                        <th>Room</th>
                        <th>Type</th>
                        <th>Subtype</th>
                        <th>Description</th>
                        <th>Timestamp</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {historyRequests.map(req => (
                        <tr key={req.id}>
                          <td data-label="Room" style={{ fontWeight: 600 }}>{req.room}</td>
                          <td data-label="Type" style={{ color: "var(--hc-gold)" }}>{req.type}</td>
                          <td data-label="Subtype" style={{ color: "rgba(255,255,255,0.45)", fontStyle: req.subtype ? "normal" : "italic" }}>
                            {req.subtype || "-"}
                          </td>
                          <td data-label="Description" className="history-desc">{req.description}</td>
                          <td data-label="Time" style={{ whiteSpace: "nowrap", fontSize: "0.82rem" }}>{formatTimestamp(req.timestamp, hf.tz, hf.locale)}</td>
                          <td data-label="Status">
                            <span className={`status-badge status-${req.status}`} style={{ fontSize: "0.7rem", padding: "4px 10px" }}>
                              {req.status === "progress" ? "In Progress" : req.status}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}

        {/* ════════════ ANALYTICS VIEW ════════════ */}
        {sidebarView === "analytics" && (
          <>
            <header className="view-header">
              <div>
                <h1>Staff Analytics</h1>
                <p style={{ color: "rgba(255,255,255,0.4)" }}>How fast your team replies, and how much they finish.</p>
              </div>
              <button className="btn-ghost" onClick={async () => {
                setAnalyticsLoading(true);
                setAnalytics(await fetchAnalytics(hotelSlug));
                setAnalyticsLoading(false);
              }}>
                <BsArrowClockwise size={14} /> Refresh
              </button>
            </header>

            {/* v2: first response and completion as separate numbers (metrics engine, hotel-local days) */}
            <V2AnalyticsStrip hotelSlug={hotelSlug} />

            {analyticsLoading && (
              <div style={{ textAlign: "center", padding: "60px", color: "rgba(255,255,255,0.3)" }}>Loading analytics…</div>
            )}

            {!analyticsLoading && !analytics && (
              <div className="glass-card" style={{ padding: "60px", textAlign: "center", color: "rgba(255,255,255,0.3)" }}>
                <LuTrendingUp size={40} style={{ marginBottom: 16, opacity: 0.4 }} />
                <p>No data yet. Numbers appear once requests are assigned and finished.</p>
              </div>
            )}

            {!analyticsLoading && analytics && (() => {
              const kpi = analytics.hotel_kpis;
              return (
                <>
                  {/* Hotel KPIs */}
                  <section style={{ display: "flex", gap: "16px", flexWrap: "wrap", marginBottom: "40px" }}>
                    {[
                      { label: "Completed Today",   value: kpi.completed_today,     color: "var(--hc-gold)", suffix: "" },
                      { label: "All-time Resolved", value: kpi.total_completed,     color: "#fff",    suffix: "" },
                      { label: "Active Right Now",  value: kpi.currently_active,    color: "var(--hc-blue)", suffix: "" },
                      { label: "Avg Resolution",    value: kpi.avg_resolution_mins != null ? fmtMins(kpi.avg_resolution_mins) : "-", color: resColor(kpi.avg_resolution_mins), suffix: "" },
                    ].map((s, i) => (
                      <div key={i} className="kpi-card">
                        <div style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.4)", marginBottom: "10px", textTransform: "uppercase", letterSpacing: "0.05em" }}>{s.label}</div>
                        <div style={{ fontSize: "2rem", fontWeight: 700, color: s.color }}>{s.value}{s.suffix}</div>
                      </div>
                    ))}
                  </section>

                  {/* Duty Legend */}
                  <div className="duty-legend" style={{ marginBottom: "32px", background: "rgba(255,255,255,0.02)", padding: "16px 20px", borderRadius: "14px", border: "1px solid rgba(255,255,255,0.05)" }}>
                    <div style={{ flex: 1 }}>
                       <div style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.4)", marginBottom: "8px" }}>STAFF ON DUTY</div>
                       <div style={{ fontSize: "1.4rem", fontWeight: 700, color: "var(--hc-success)" }}>
                         {analytics.staff.filter(s => s.is_online).length} <span style={{ fontSize: "0.9rem", opacity: 0.5, fontWeight: 400 }}>of {analytics.staff.length} staff</span>
                       </div>
                    </div>
                    <div style={{ flex: 1 }}>
                       <div style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.4)", marginBottom: "8px" }}>TOTAL WORK TIME (TODAY)</div>
                       <div style={{ fontSize: "1.4rem", fontWeight: 700, color: "var(--hc-gold)" }}>
                         {fmtMins(analytics.staff.reduce((acc, s) => acc + (s.today_work_mins || 0), 0))}
                       </div>
                    </div>
                    <div style={{ flex: 1 }}>
                       <div style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.4)", marginBottom: "8px" }}>ALL-TIME WORK TIME</div>
                       <div style={{ fontSize: "1.4rem", fontWeight: 700, color: "#fff" }}>
                         {fmtMins(analytics.staff.reduce((acc, s) => acc + (s.total_work_mins || 0), 0))}
                       </div>
                    </div>
                  </div>

                  {/* ════════ Trend, speed and mix ════════
                      Same colour language as the dashboard band, so a manager
                      reads gold/blue/green once and it holds everywhere. */}
                  <section style={{ marginBottom: "20px" }}>
                    <ChartCard
                      title="Last seven days"
                      hint="Requests per day, split by where they ended up. A thick gold or blue band on a past day means work carried over."
                      legend={STATUS_SERIES}
                    >
                      <ColumnChart
                        data={insights.daily}
                        series={STATUS_SERIES}
                        height={200}
                        ariaLabel="Requests per day over the last seven days, split by status"
                        emptyNote="No requests in the last seven days."
                      />
                    </ChartCard>
                  </section>

                  <div className="insight-grid insight-grid--even" style={{ marginBottom: "36px" }}>
                    <ChartCard
                      title="How long requests take"
                      hint={
                        insights.resolvedCount === 0
                          ? "Appears once staff accept and finish requests."
                          : `${insights.withinTarget}% of ${insights.resolvedCount} resolved requests landed inside 10 minutes · median ${fmtMins(insights.medianMins)}.`
                      }
                    >
                      <ColumnChart
                        data={insights.dist}
                        series={[{ label: "Requests", color: CHART_NEW }]}
                        height={192}
                        emphasis={(_, i) => (i <= 1 ? CHART_NEW : CHART_QUIET)}
                        divider={{ afterIndex: 1, label: "10-min target" }}
                        ariaLabel="Distribution of how long resolved requests took, in minutes"
                        emptyNote="No resolved requests with timing yet."
                      />
                    </ChartCard>

                    <ChartCard
                      title="Service mix"
                      hint="What guests ask for most, all time. Everything past the top six folds into Other."
                    >
                      <HBarChart
                        rows={insights.mixRows}
                        series={[{ label: "Requests", color: CHART_NEW }]}
                        ariaLabel="Most requested services by volume"
                        emptyNote="No requests recorded yet."
                      />
                    </ChartCard>
                  </div>

                  {/* Legend */}
                  <div style={{ display: "flex", gap: "16px", marginBottom: "20px", fontSize: "0.75rem", color: "rgba(255,255,255,0.35)", alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 600, color: "rgba(255,255,255,0.5)" }}>Resolution time:</span>
                    {[{ color: "var(--hc-success)", label: "≤ 10 min (fast)" }, { color: "var(--hc-pending)", label: "10-30 min" }, { color: "#ef4444", label: "> 30 min (slow)" }].map(l => (
                      <span key={l.label} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                        <span style={{ width: 8, height: 8, borderRadius: "50%", background: l.color, display: "inline-block" }} />{l.label}
                      </span>
                    ))}
                  </div>

                  {/* Staff performance list */}
                  {analytics.staff.length === 0 ? (
                    <div className="glass-card" style={{ padding: "60px", textAlign: "center", color: "rgba(255,255,255,0.3)" }}>
                      No staff registered yet. Add staff in the Staff Registry to see analytics.
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                      {(() => { const teamBestToday = Math.max(0, ...analytics.staff.map(m => m.completed_today)); return analytics.staff.map((s, idx) => {
                        const deptColor = DEPT_COLORS[s.department] || "#94a3b8";
                        const isExpanded = expandedStaff === s.id;
                        return (
                          <div key={s.id} className="staff-perf-row">
                            {/* Summary row - click to expand */}
                            <div
                              style={{ padding: "18px 22px", cursor: "pointer", display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap" }}
                              onClick={() => setExpandedStaff(isExpanded ? null : s.id)}
                            >
                              {/* Rank + avatar */}
                              <div style={{ display: "flex", alignItems: "center", gap: "12px", minWidth: "200px", flex: "1 1 200px" }}>
                                <div style={{ fontSize: "0.8rem", fontWeight: 700, color: "rgba(255,255,255,0.2)", width: "20px" }}>#{idx + 1}</div>
                                <div style={{ width: "38px", height: "38px", borderRadius: "50%", background: `${deptColor}25`, border: `1px solid ${deptColor}44`, display: "flex", alignItems: "center", justifyContent: "center", color: deptColor, fontWeight: 700, fontSize: "1rem", flexShrink: 0 }}>
                                  {s.name.charAt(0)}
                                </div>
                                <div style={{ flex: 1, minWidth: 0 }}>
                                  <div style={{ fontWeight: 600, fontSize: "0.95rem" }}>{s.name}</div>
                                  <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.35)" }}>{s.role} · <span style={{ color: deptColor }}>{s.department}</span></div>
                                  {/* Share of the busiest person's day - reads at a glance without adding a column */}
                                  {teamBestToday > 0 && (
                                    <ShareMeter
                                      value={s.completed_today}
                                      max={teamBestToday}
                                      color={CHART_NEW}
                                      title={`${s.completed_today} completed today, against the team's best of ${teamBestToday}`}
                                    />
                                  )}
                                </div>
                              </div>

                              {/* Stats */}
                              <div className="staff-stats">
                                <div style={{ textAlign: "center" }}>
                                  <div style={{ fontSize: "1.3rem", fontWeight: 700, color: "var(--hc-gold)" }}>{s.completed_today}</div>
                                  <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)" }}>today</div>
                                </div>
                                <div style={{ textAlign: "center" }}>
                                  <div style={{ fontSize: "1.3rem", fontWeight: 700, color: "#fff" }}>{s.total_completed}</div>
                                  <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)" }}>all-time</div>
                                </div>
                                <div style={{ textAlign: "center" }}>
                                  <div style={{ fontSize: "1.3rem", fontWeight: 700, color: resColor(s.avg_resolution_mins) }}>{fmtMins(s.avg_resolution_mins)}</div>
                                  <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)" }}>avg time</div>
                                </div>
                                <div style={{ textAlign: "center" }}>
                                  <div style={{ fontSize: "1.3rem", fontWeight: 700, color: "var(--hc-success)" }}>{fmtMins(s.fastest_mins)}</div>
                                  <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)" }}>fastest</div>
                                </div>
                                <div style={{ textAlign: "center" }}>
                                  <div style={{ fontSize: "1.3rem", fontWeight: 700, color: s.currently_active > 0 ? "var(--hc-blue)" : "rgba(255,255,255,0.25)" }}>{s.currently_active}</div>
                                  <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)" }}>active</div>
                                </div>
                                <div style={{ textAlign: "center", minWidth: "80px" }}>
                                  <div style={{ fontSize: "1.3rem", fontWeight: 700, color: "var(--hc-gold)" }}>{fmtMins(s.today_work_mins)}</div>
                                  <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)" }}>today&apos;s time</div>
                                </div>
                                <div style={{ textAlign: "center", minWidth: "80px" }}>
                                  <div style={{ fontSize: "1.3rem", fontWeight: 700, color: "#fff" }}>{fmtMins(s.total_work_mins)}</div>
                                  <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)" }}>all-time</div>
                                </div>
                                <div style={{ textAlign: "center" }}>
                                  <div style={{ width: "10px", height: "10px", borderRadius: "50%", background: s.is_online ? "var(--hc-success)" : "rgba(255,255,255,0.1)", margin: "0 auto 4px", boxShadow: s.is_online ? "0 0 10px #16A34A" : "none" }} />
                                  <div style={{ fontSize: "0.68rem", color: s.is_online ? "var(--hc-success)" : "rgba(255,255,255,0.25)", fontWeight: 600 }}>{s.is_online ? "ONLINE" : "OFFLINE"}</div>
                                </div>
                                <div style={{ color: "rgba(255,255,255,0.3)", flexShrink: 0 }}>
                                  {isExpanded ? <LuChevronUp size={16} /> : <LuChevronDown size={16} />}
                                </div>
                              </div>
                            </div>

                            {/* Expanded - activity log */}
                            {isExpanded && (
                              <div className="log-panel" style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
                                {/* Task type breakdown */}
                                {Object.keys(s.task_type_breakdown).length > 0 && (
                                  <div style={{ padding: "14px 22px 12px", display: "flex", gap: "8px", flexWrap: "wrap", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                                    <span style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.35)", alignSelf: "center", marginRight: 4 }}>Task breakdown:</span>
                                    {Object.entries(s.task_type_breakdown).sort((a, b) => b[1] - a[1]).map(([type, count]) => (
                                      <span key={type} style={{ fontSize: "0.72rem", padding: "2px 8px", borderRadius: "6px", background: "rgba(225, 175, 104,0.1)", color: "var(--hc-gold)" }}>
                                        {type} · {count}
                                      </span>
                                    ))}
                                  </div>
                                )}

                                {/* Activity log table */}
                                {s.activity_log.length === 0 ? (
                                  <div style={{ padding: "28px 22px", color: "rgba(255,255,255,0.3)", fontSize: "0.88rem" }}>No activity yet.</div>
                                ) : (
                                  <div className="activity-scroll">
                                    {/* Table header */}
                                    <div className="activity-grid" style={{ padding: "10px 22px", fontSize: "0.7rem", color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: "0.05em", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                                      <span>Room</span>
                                      <span>Task</span>
                                      <span>Priority</span>
                                      <span style={{ display: "flex", alignItems: "center", gap: 4 }}><LuClock size={11} /> Notified</span>
                                      <span style={{ display: "flex", alignItems: "center", gap: 4 }}><LuCheck size={11} /> Done at</span>
                                      <span>Time taken</span>
                                    </div>
                                    {s.activity_log.map(entry => {
                                      const rc = resColor(entry.resolution_mins ?? undefined);
                                      return (
                                        <div key={entry.id} className="activity-row activity-grid" style={{ padding: "12px 22px", fontSize: "0.83rem", alignItems: "center" }}>
                                          <span style={{ fontWeight: 600, color: "var(--hc-gold)" }}>Rm {entry.room}</span>
                                          <span style={{ color: "rgba(255,255,255,0.8)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", paddingRight: 12 }}>
                                            {entry.type}{entry.subtype ? ` · ${entry.subtype}` : ""}
                                          </span>
                                          <span>
                                            <span style={{ fontSize: "0.68rem", padding: "2px 8px", borderRadius: "100px", background: entry.priority === "high" ? "rgba(220,38,38,0.15)" : entry.priority === "medium" ? "rgba(245,158,11,0.15)" : "rgba(99,102,241,0.15)", color: entry.priority === "high" ? "#ef4444" : entry.priority === "medium" ? "var(--hc-pending)" : "#818cf8" }}>
                                              {entry.priority}
                                            </span>
                                          </span>
                                          <span style={{ color: "rgba(255,255,255,0.45)", fontSize: "0.78rem" }}>
                                            {entry.notified_at ? formatTimestamp(entry.notified_at, hf.tz, hf.locale) : <span style={{ opacity: 0.3 }}>-</span>}
                                          </span>
                                          <span style={{ color: entry.done_at ? "rgba(255,255,255,0.45)" : "rgba(255,255,255,0.2)", fontSize: "0.78rem" }}>
                                            {entry.done_at ? formatTimestamp(entry.done_at, hf.tz, hf.locale) : (
                                              <span style={{ color: entry.status === "progress" ? "var(--hc-pending)" : "rgba(255,255,255,0.25)", fontSize: "0.72rem" }}>
                                                {entry.status === "progress" ? "In progress…" : "-"}
                                              </span>
                                            )}
                                          </span>
                                          <span style={{ fontWeight: 600, color: rc, fontSize: "0.82rem" }}>
                                            {fmtMins(entry.resolution_mins ?? undefined)}
                                          </span>
                                        </div>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      }); })()}
                    </div>
                  )}
                </>
              );
            })()}
          </>
        )}

        {/* ════════════ ROOM TIMELINE VIEW ════════════ */}
        {sidebarView === "room-timeline" && timelineRoom && (() => {
            const roomProfile = guestRooms[timelineRoom];
            const stayStart = new Date(roomProfile.checkIn!).setHours(0,0,0,0);
            const stayEnd   = new Date(roomProfile.checkOut!).setHours(23,59,59,999);
            const roomReqs = requests
              .filter(r => r.room === timelineRoom && r.timestamp >= stayStart && r.timestamp <= stayEnd)
              .sort((a, b) => b.timestamp - a.timestamp);
            
            return (
              <>
                <header className="view-header">
                  <div>
                    <h1>Room {timelineRoom}</h1>
                    <p style={{ color: "rgba(255,255,255,0.4)" }}>This guest&apos;s requests and profile.</p>
                  </div>
                  <button className="btn-ghost" onClick={() => setSidebarView("guests")}>Back to Guest List</button>
                </header>

                <div className="timeline-grid">
                   {/* Guest Profile Summary */}
                   <div className="glass-card" style={{ padding: "24px", height: "fit-content" }}>
                      <h3 style={{ marginBottom: "20px", color: "var(--hc-gold)", display: "flex", alignItems: "center", gap: "8px" }}><BsPersonCircle /> Guest Identity</h3>
                      {roomProfile ? (
                         <div style={{ fontSize: "0.9rem", color: "rgba(255,255,255,0.7)", display: "flex", flexDirection: "column", gap: "16px" }}>
                            <div>
                              {roomProfile.guests?.map(g => <div key={g.name} style={{ marginBottom: "6px" }}><strong style={{ color: "#fff" }}>{g.name}</strong> <span style={{ opacity: 0.6 }}>({g.type}) {g.age && `Age: ${g.age}`}</span></div>)}
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: "8px", borderTop: "1px solid rgba(255,255,255,0.1)", paddingTop: "16px" }}>
                               <div><strong style={{ color: "rgba(255,255,255,0.5)" }}>Stay Type:</strong> {roomProfile.stayType}</div>
                               <div><strong style={{ color: "rgba(255,255,255,0.5)" }}>Loyalty:</strong> {roomProfile.loyaltyTier}</div>
                               <div><strong style={{ color: "rgba(255,255,255,0.5)" }}>Check-In:</strong> {roomProfile.checkIn ? formatDateShort(roomProfile.checkIn, hf.tz, hf.locale) : "-"}</div>
                               <div><strong style={{ color: "rgba(255,255,255,0.5)" }}>Check-Out:</strong> {roomProfile.checkOut ? formatDateShort(roomProfile.checkOut, hf.tz, hf.locale) : "-"}</div>
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: "8px", borderTop: "1px solid rgba(255,255,255,0.1)", paddingTop: "16px" }}>
                              <div><strong style={{ color: "rgba(255,255,255,0.5)" }}>Preferences:</strong> {roomProfile.preferences?.tags?.length ? roomProfile.preferences.tags.join(", ") : "None"}</div>
                            </div>
                         </div>
                      ) : (
                         <p style={{ color: "rgba(255,255,255,0.4)" }}>No guest profile saved for this room.</p>
                      )}
                   </div>

                   {/* Timeline */}
                   <div>
                       <h3 style={{ marginBottom: "20px", color: "#fff", display: "flex", alignItems: "center", gap: "8px" }}><BsClockHistory /> Request Timeline</h3>
                       {roomReqs.length === 0 ? (
                          <div className="glass-card" style={{ padding: "40px", textAlign: "center", color: "rgba(255,255,255,0.3)" }}>
                             No service requests found for this stay.
                          </div>
                       ) : (
                          <div style={{ display: "flex", flexDirection: "column", gap: "16px", position: "relative" }}>
                             {/* Vertical line connecting timeline events */}
                             <div style={{ position: "absolute", left: "20px", top: "20px", bottom: "20px", width: "2px", background: "rgba(255,255,255,0.05)", zIndex: 0 }} />
                             
                             {roomReqs.map(req => (
                                <div key={req.id} className="glass-card" style={{ padding: "16px 20px", position: "relative", zIndex: 1, marginLeft: "40px" }}>
                                   <div style={{ position: "absolute", left: "-26px", top: "20px", width: "10px", height: "10px", borderRadius: "50%", background: req.status === "completed" ? "var(--hc-success)" : req.status === "progress" ? "var(--hc-pending)" : "var(--hc-gold)", boxShadow: "0 0 0 4px var(--hc-slate)" }} />
                                   
                                   <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "8px" }}>
                                      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                                         <span style={{ fontWeight: 600, color: "var(--hc-gold)", fontSize: "1.1rem" }}>{req.type}</span>
                                         {req.subtype && <span style={{ color: "rgba(255,255,255,0.4)", fontStyle: "italic", fontSize: "0.85rem" }}>· {req.subtype}</span>}
                                      </div>
                                      <span style={{ fontSize: "0.8rem", color: "rgba(255,255,255,0.4)" }}>{formatTimestamp(req.timestamp, hf.tz, hf.locale)}</span>
                                   </div>
                                   <p style={{ fontSize: "0.95rem", marginBottom: "16px", color: "rgba(255,255,255,0.8)" }}>{req.description}</p>
                                   
                                   <div style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.5)", display: "flex", flexWrap: "wrap", gap: "16px", background: "rgba(255,255,255,0.02)", padding: "12px", borderRadius: "8px" }}>
                                      <span style={{ display: "flex", alignItems: "center", gap: "6px" }}><strong style={{ color: "rgba(255,255,255,0.3)" }}>Status:</strong> <span className={`status-badge status-${req.status}`} style={{ transform: "scale(0.8)", display: "inline-block", transformOrigin: "left" }}>{req.status}</span></span>
                                      {req.assigned_to && <span style={{ display: "flex", alignItems: "center", gap: "6px" }}><strong style={{ color: "rgba(255,255,255,0.3)" }}>Assigned:</strong> {req.assigned_to}</span>}
                                      {req.accepted_at && <span style={{ display: "flex", alignItems: "center", gap: "6px" }}><strong style={{ color: "rgba(255,255,255,0.3)" }}>Accepted:</strong> {formatTimestamp(req.accepted_at, hf.tz, hf.locale)}</span>}
                                      {req.resolved_at && <span style={{ display: "flex", alignItems: "center", gap: "6px" }}><strong style={{ color: "rgba(255,255,255,0.3)" }}>Resolved:</strong> {formatTimestamp(req.resolved_at, hf.tz, hf.locale)}</span>}
                                   </div>
                                </div>
                             ))}
                          </div>
                       )}
                   </div>
                </div>
              </>
            );
        })()}

        {/* ════════════ STAFF REGISTRY VIEW ════════════ */}
        {sidebarView === "staff" && (
          <>
            <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "40px", flexWrap: "wrap", gap: "20px" }}>
              <div>
                <h1 style={{ fontSize: "2.4rem", marginBottom: "8px" }}>Staff Registry</h1>
                <p style={{ color: "rgba(255,255,255,0.4)" }}>Your staff. New requests go automatically to the right free person.</p>
              </div>
              <div style={{ display: "flex", gap: "12px" }}>
                {staffList.length === 0 && (
                  <button className="btn-ghost" onClick={seedStaff}>
                    <LuSparkles size={14} /> Load Demo Staff
                  </button>
                )}
                <button className="btn-complete" onClick={openAddStaff}>
                  <BsPlusLg size={13} /> Add Staff
                </button>
              </div>
            </header>

            {/* Department breakdown summary */}
            {staffList.length > 0 && (
              <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", marginBottom: "32px" }}>
                {DEPARTMENTS.map(dept => {
                  const count = staffList.filter(s => s.department === dept && s.is_active).length;
                  if (count === 0) return null;
                  const color = DEPT_COLORS[dept] || "#94a3b8";
                  return (
                    <div key={dept} style={{ padding: "6px 14px", borderRadius: "100px", background: `${color}15`, border: `1px solid ${color}33`, fontSize: "0.82rem", color }}>
                      {dept} · {count}
                    </div>
                  );
                })}
              </div>
            )}

            {staffList.length === 0 ? (
              <div className="glass-card" style={{ padding: "80px", textAlign: "center", color: "rgba(255,255,255,0.2)" }}>
                <div style={{ fontSize: "3rem", marginBottom: "16px" }}><LuUsers /></div>
                <p style={{ fontSize: "1rem" }}>No staff registered yet</p>
                <p style={{ fontSize: "0.85rem", marginTop: "8px", color: "rgba(255,255,255,0.12)" }}>Add staff so requests can be assigned automatically.</p>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
                {DEPARTMENTS.map(dept => {
                  const members = staffList.filter(s => s.department === dept);
                  if (members.length === 0) return null;
                  const color = DEPT_COLORS[dept] || "#94a3b8";
                  // Count active tasks per staff
                  const activeTasks: Record<string, number> = {};
                  requests.filter(r => r.status === "progress").forEach(r => {
                    if (r.assigned_to) activeTasks[r.assigned_to] = (activeTasks[r.assigned_to] || 0) + 1;
                  });
                  return (
                    <div key={dept}>
                      <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "14px" }}>
                        <div style={{ width: "3px", height: "18px", background: color, borderRadius: "2px" }} />
                        <h3 style={{ fontSize: "1rem", fontWeight: 700, color }}>{dept}</h3>
                        <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.3)" }}>{members.filter(m => m.is_active).length} active</span>
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(280px, 100%), 1fr))", gap: "12px" }}>
                        {members.map(member => {
                          const taskCount = activeTasks[member.name] || 0;
                          return (
                            <div key={member.id} className="glass-card" style={{ padding: "18px 20px", opacity: member.is_active ? 1 : 0.45 }}>
                              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "10px" }}>
                                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                                  <div style={{ width: "36px", height: "36px", borderRadius: "50%", background: `${color}25`, border: `1px solid ${color}44`, display: "flex", alignItems: "center", justifyContent: "center", color, fontSize: "1rem", fontWeight: 700 }}>
                                    {member.name.charAt(0)}
                                  </div>
                                  <div>
                                    <div style={{ fontWeight: 600, fontSize: "0.95rem" }}>{member.name}</div>
                                    <div style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.4)" }}>{member.role}</div>
                                  </div>
                                </div>
                                <div style={{ display: "flex", gap: "4px" }}>
                                  {/* Personal links are retired: pair a floor device, set a PIN, plan the roster. */}
                                  <StaffDeviceActions hotelSlug={hotelSlug} member={member} />
                                  <button onClick={() => openEditStaff(member)} style={{ background: "none", border: "none", color: "rgba(255,255,255,0.3)", cursor: "pointer", padding: "4px" }}><LuPencil size={14} /></button>
                                  <button onClick={() => deleteStaff(member.id)} style={{ background: "none", border: "none", color: "rgba(239,68,68,0.4)", cursor: "pointer", padding: "4px" }}><LuTrash2 size={14} /></button>
                                </div>
                              </div>
                              <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.78rem" }}>
                                <span style={{
                                  padding: "2px 8px", borderRadius: "100px",
                                  background: taskCount > 0 ? "rgba(245,158,11,0.15)" : "rgba(22,163,74,0.15)",
                                  color: taskCount > 0 ? "var(--hc-pending)" : "var(--hc-success)",
                                  border: `1px solid ${taskCount > 0 ? "rgba(245,158,11,0.3)" : "rgba(22,163,74,0.3)"}`,
                                }}>
                                  {taskCount > 0 ? `${taskCount} active task${taskCount > 1 ? "s" : ""}` : "Free"}
                                </span>
                                <span style={{
                                  padding: "2px 8px", borderRadius: "100px",
                                  background: member.is_online ? "rgba(22,163,74,0.12)" : "rgba(255,255,255,0.05)",
                                  color: member.is_online ? "var(--hc-success)" : "rgba(255,255,255,0.25)",
                                  fontWeight: 600
                                }}>
                                  {member.is_online ? "● Online" : "● Offline"}
                                </span>
                                {!member.is_active && <span style={{ color: "rgba(255,255,255,0.3)" }}>Inactive</span>}
                                {member.phone_number && (
                                  <span style={{
                                    padding: "2px 8px", borderRadius: "100px",
                                    background: "rgba(255,255,255,0.05)",
                                    color: "rgba(255,255,255,0.35)",
                                    fontSize: "0.72rem",
                                    display: "inline-flex", alignItems: "center", gap: "4px",
                                  }}>
                                    📱 {member.phone_number.replace(/^(\+?91)?(\d{2})\d{4}(\d{4})$/, "$2****$3")}
                                  </span>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}

      </main>

      {/* Modals */}
      <SvcStyles />
      {detailRequest && (
        <RequestDetail hotelSlug={hotelSlug} request={detailRequest} onClose={() => setDetailId(null)} />
      )}
      <Drawer open={showPhoneEntry} onClose={() => setShowPhoneEntry(false)} title="New request" wide>
        <PhoneEntryConsole
          compact
          onFiled={card => {
            const next = cleanCategory(card as unknown as Request);
            setRequests(prev => [next, ...prev.filter(r => r.id !== next.id)]);
          }}
          onDone={() => setShowPhoneEntry(false)}
        />
      </Drawer>
      {showModal && (
        <ManualEntryModal
          onClose={() => setShowModal(false)}
          onSubmit={handleManualSubmit}
        />
      )}

      {showGuestModal && (
        <GuestModal
          initial={editingGuestData || undefined}
          onClose={() => { setShowGuestModal(false); setEditingGuestData(null); }}
          onSave={async (data) => {
            if (editingGuestData && editingGuestData.roomNumber !== data.roomNumber) {
              await fetch(`${API_BASE}/hotels/${hotelSlug}/guests/${editingGuestData.roomNumber}`, { method: "DELETE" });
            }
            await fetch(`${API_BASE}/hotels/${hotelSlug}/guests/${data.roomNumber}`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ data }),
            });
            setGuestRooms(await fetchGuestRooms(hotelSlug));
            setShowGuestModal(false);
            setEditingGuestData(null);
          }}
        />
      )}

      {/* Staff Add/Edit Modal */}
      {showStaffModal && (
        <div className="modal-overlay" onClick={e => e.target === e.currentTarget && setShowStaffModal(false)}>
          <div className="modal-box" style={{ maxWidth: "460px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "28px" }}>
              <div>
                <h2 style={{ fontSize: "1.5rem", marginBottom: "6px" }}>{editingStaff ? "Edit Staff Member" : "Add Staff Member"}</h2>
                <p style={{ color: "rgba(255,255,255,0.4)", fontSize: "0.88rem" }}>Will be included in auto-assignment pool</p>
              </div>
              <button onClick={() => setShowStaffModal(false)} style={{ background: "none", border: "none", color: "rgba(255,255,255,0.4)", cursor: "pointer" }}><BsX size={24} /></button>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
              <div>
                <label style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "8px" }}>FULL NAME</label>
                <input className="form-input" placeholder="e.g. Ravi Kumar" value={staffForm.name} onChange={e => setStaffForm(f => ({ ...f, name: e.target.value }))} />
              </div>
              <div>
                <label style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "8px" }}>ROLE / DESIGNATION</label>
                <input className="form-input" placeholder="e.g. Housekeeping Supervisor" value={staffForm.role} onChange={e => setStaffForm(f => ({ ...f, role: e.target.value }))} />
              </div>
              <div>
                <label style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "8px" }}>DEPARTMENT</label>
                <select className="form-select" value={staffForm.department} onChange={e => setStaffForm(f => ({ ...f, department: e.target.value }))}>
                  {DEPARTMENTS.map(d => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
              <div style={{ display: "flex", gap: "20px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <input type="checkbox" id="staff-active" checked={staffForm.is_active} onChange={e => setStaffForm(f => ({ ...f, is_active: e.target.checked }))} style={{ width: "16px", height: "16px", accentColor: "var(--hc-gold)" }} />
                  <label htmlFor="staff-active" style={{ fontSize: "0.88rem", color: "rgba(255,255,255,0.6)", cursor: "pointer" }}>Active</label>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <input type="checkbox" id="staff-online" checked={staffForm.is_online} onChange={e => setStaffForm(f => ({ ...f, is_online: e.target.checked }))} style={{ width: "16px", height: "16px", accentColor: "var(--hc-success)" }} />
                  <label htmlFor="staff-online" style={{ fontSize: "0.88rem", color: "rgba(255,255,255,0.6)", cursor: "pointer" }}>On Duty</label>
                </div>
              </div>
              <div>
                <label style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", display: "block", marginBottom: "8px" }}>PHONE NUMBER <span style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.2)" }}>(for SMS alerts)</span></label>
                <input className="form-input" type="tel" placeholder="e.g. 9876543210" value={staffForm.phone_number} onChange={e => setStaffForm(f => ({ ...f, phone_number: e.target.value }))} />
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "12px", marginTop: "28px" }}>
              <button className="btn-ghost" onClick={() => setShowStaffModal(false)}>Cancel</button>
              <button className="btn-complete" onClick={saveStaff}><LuCheck size={16} /> {editingStaff ? "Save Changes" : "Add Staff"}</button>
            </div>
          </div>
        </div>
      )}


    </div>
  );
}
