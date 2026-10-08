# Clefs web app (`client/`)

The Next.js 16 app for Clefs: the marketing site and lead magnets, the owner and staff console, the guest pages, and the paired-device PWA. It talks to the FastAPI server in `../server` through a same-origin rewrite. Project overview and full local setup: [../README.md](../README.md). Architecture: [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).

## Run it

```sh
npm install
CLEFS_API_ORIGIN=http://localhost:8000 npm run dev      # http://localhost:3000
```

The API must be running on port 8000 (see the root README). `npm run dev:https` serves over HTTPS on the LAN, for testing the guest page and the device app on a phone.

## Environment

| Variable | Purpose |
|---|---|
| `CLEFS_API_ORIGIN` | Server-only. Where Next sends `/api/clefs/*` (and the realtime mint proxy in `app/api/realtime`). Default `http://localhost:8000`. |
| `NEXT_PUBLIC_API_URL` | The API origin as the browser reaches it, for SSE streams and large uploads (60-second signed tickets). Default: `CLEFS_API_ORIGIN`. Keep it local in development. |
| `NEXT_PUBLIC_VAPID_PUBLIC_KEY` | Web push for staff devices (matches the API's key). |
| `NEXT_PUBLIC_AVATAR_RIG` | Unset: the video avatar. `viseme` only to evaluate the lip-sync rig. |
| `NEXT_PUBLIC_STAFF_LINK_SUNSET`, `NEXT_PUBLIC_SERVICE_CLASSIC_ENTRY` | Legacy staff-link retirement date and the classic request-entry switch. Leave unset. |

The browser only calls `/api/clefs/*` on its own origin, so the `clefs_session` and `clefs_device` cookies are first-party everywhere (localhost, previews, production) without CORS credentials.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Development server |
| `npm run build` / `npm run start` | Production build and server |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | ESLint (flat config) |
| `npm run test` | `node --test` on `app/**/*.test.mjs`: pure helpers (autonomy dial, device outbox, view states). To run one file under `app/app/[hotelSlug]/...`, pass the full file path: a folder path makes `[hotelSlug]` a glob class and finds nothing. |
| `npm run check:claims` | The claims linter: every market statistic in marketing copy must come from `app/lib/claims.ts` and be rendered through `<Claim>` / `claimText()` or tagged `// claim: <id>`; banned, unverified, expired or off-surface claims fail. `--self-test` runs its fixtures. |
| `node scripts/check-i18n.mjs` | Every guest dictionary in `app/stay/i18n/` carries every key and placeholder of `en.ts`, with no em dashes. |

Run `typecheck`, `lint`, `test`, `check:claims` and the i18n check before a deploy; `build` does not run them.

## Where things are

| Path | What |
|---|---|
| `app/page.tsx`, `app/MarketingHomepage.tsx`, solution pages | Marketing site ("For hotels in the UAE") |
| `app/free-ota-audit/`, `app/google-check/`, `app/pilot/` | Lead magnets and the pilot page |
| `app/security/`, `app/privacy/`, `app/dpa/`, `app/subprocessors/` | Trust pages; the residency statement lives in `app/lib/subprocessors.ts` |
| `app/login/`, `app/auth/` | Password login and magic-link exchange |
| `app/app/[hotelSlug]/(console)/` | The console: `brief`, `profit`, `demand`, `inquiries`, `service`, `concierge` (and `voice`), `amenities`, `menu`, `reviews`, `guests` (and `journeys`), `visibility`, `finance`, `ask`, `trust`, `settings` |
| `app/device/[hotelSlug]/` | The paired-device PWA for staff (PIN shifts, handover, requests) |
| `app/stay/[hotelSlug]/[roomId]/` | The guest page: concierge (voice avatar and chat), requests, menu, tracker; 8 languages including Arabic (RTL) |
| `app/g/`, `app/q/`, `app/w/inquiry/`, `app/brief/[token]` | Journey pages, quote pages, the embeddable inquiry form (loader `public/w/inquiry.js`), the owner's mobile brief |
| `app/lib/` | `api.ts` (`apiFetch`), `format.ts` (hotel currency and timezone), `session.tsx` (`can(perm)`), `types.ts` (server serializer mirrors), `claims.ts` (the claims registry) |
| `app/ui/` | The UI kit: core, forms, table, overlay, charts, upload, toast |

## Conventions

- **This is Next.js 16.** Read the relevant guide in `node_modules/next/dist/docs/` before writing routing code (route groups, async `params`, metadata). See `AGENTS.md`.
- **No Tailwind.** Each surface injects one CSS string with its own class prefix (`svc-`, `cc-`, `ups-`, `rev-`, `gst-`, `trs-`, `mk-`, ...), using the `--clefs-*` tokens and the `app/ui` kit.
- **Frozen shared files** (`next.config.mjs`, `proxy.ts`, `package.json`, `tsconfig.json`, `app/globals.css`, `app/lib/{api,format,session,hotel,i18n,types}`, `app/ui/**`, the console `layout.tsx`, `ConsoleShell.tsx` and `nav.ts`) change only as their own reviewed change.
- **Access is decided by the server.** Pages gate UI with `can(perm)` from the session's permission list; they never infer access from a role name.
- **No em dash characters** in code or copy.

## Protected avatar code

The guest concierge avatar's lip-sync is fragile and has regressed before. The mouth gates on **live audio RMS**, never on transcript deltas, and the viseme rig stays **off** by default. Do not edit `app/lib/visemeRig.ts`, `VISEMES.md`, `scripts/*visemes*`, `avatar-source/**` or `public/avatar/**`, and keep the avatar, audio-envelope and mouth-gating blocks of `app/ClefsConcierge.tsx` and the hero greeting and portrait blocks of `app/MarketingHomepage.tsx` byte-identical. Any change near them needs the manual avatar QA pass: a voice turn, barge-in, the text toggle, the idle mic auto-close, iOS Safari and Android Chrome, with the rig on and off.
