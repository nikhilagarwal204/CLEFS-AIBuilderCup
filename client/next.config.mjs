import { readFileSync } from 'node:fs';

// Where the FastAPI server lives, as seen FROM THE NEXT SERVER. Server-only on
// purpose (no NEXT_PUBLIC_ prefix): the browser never learns this origin for
// ordinary calls, it talks to /api/clefs on the web origin and Next proxies.
// That is what makes the clefs_session / clefs_device cookies first-party on
// www, on every preview deployment and on localhost alike, with no CORS
// credentials and no cookie Domain juggling.
const CLEFS_API_ORIGIN = (process.env.CLEFS_API_ORIGIN || 'http://localhost:8000').replace(/\/+$/, '');
// The API origin as the BROWSER reaches it (tickets, SSE, large uploads).
const PUBLIC_API_ORIGIN = (process.env.NEXT_PUBLIC_API_URL || CLEFS_API_ORIGIN).replace(/\/+$/, '');

// Rewrites are resolved by `next build` into .next/routes-manifest.json, so
// `CLEFS_API_ORIGIN=... next start` on an existing build still proxies to the
// origin of the BUILD (QA OWN-11 / PUB-2: a stack on :8811 silently talked to
// :8000). next start still evaluates this file, so say it out loud there.
function warnIfRewriteBakedElsewhere(phase) {
    if (phase !== 'phase-production-server') return;
    try {
        const manifest = JSON.parse(readFileSync(new URL('./.next/routes-manifest.json', import.meta.url), 'utf8'));
        const r = manifest.rewrites;
        const all = Array.isArray(r) ? r : [...(r?.beforeFiles || []), ...(r?.afterFiles || []), ...(r?.fallback || [])];
        const baked = all.find((x) => x.source === '/api/clefs/:path*')?.destination;
        if (baked && !baked.startsWith(`${CLEFS_API_ORIGIN}/`)) {
            console.warn(
                `[clefs] /api/clefs is built to proxy to ${baked.replace(/\/:path\*$/, '')}, not CLEFS_API_ORIGIN=${CLEFS_API_ORIGIN}. ` +
                'Rewrites are fixed at build time: rebuild with CLEFS_API_ORIGIN set.',
            );
        }
    } catch {
        // No manifest (dev) or an unexpected shape: nothing to compare.
    }
}

/** @type {import('next').NextConfig} */
const nextConfig = {
    experimental: {
        serverActions: {
            allowedOrigins: ['192.168.1.166'],
        },
    },
    async redirects() {
        return [
            // The AI Builder Cup prototype is the guest service desk: these
            // pages front modules switched off with CLEFS_DISABLED_MODULES, so
            // they go to the homepage (temporary, so switching a module back on
            // only means dropping its line here).
            ...["/profit-guard", "/demand-radar", "/demand-preview", "/free-ota-audit", "/google-check", "/brief"].flatMap(
                (path) => [
                    { source: path, destination: "/", permanent: false },
                    { source: `${path}/:rest*`, destination: "/", permanent: false },
                ],
            ),
            {
                // The guest concierge moved /aria/... -> /stay/... during the
                // Aria -> Clefs rebrand. QR codes already printed and mounted in
                // partner hotel rooms encode the /aria/ path, so this redirect is
                // PERMANENT infrastructure, not a temporary migration step.
                // Do not remove it.
                source: '/aria/:hotelSlug/:roomId',
                destination: '/stay/:hotelSlug/:roomId',
                permanent: true,
            },
            {
                // SSE must never ride the /api/clefs proxy: with a browser's
                // Accept-Encoding the proxied response is gzipped and buffered,
                // so no event arrives at all (measured locally; without gzip it
                // streams, but browsers always ask for it). Code that still builds the stream URL from
                // API_PREFIX (the legacy staff phone page) is bounced straight
                // to the API origin; EventSource follows redirects and the
                // query string (a ?t= ticket) is carried over. Temporary, so no
                // browser caches it.
                source: '/api/clefs/hotels/:slug/stream',
                destination: `${PUBLIC_API_ORIGIN}/hotels/:slug/stream`,
                permanent: false,
            },
        ];
    },
    async headers() {
        return [
            {
                // The public quote page carries an Accept button (a binding
                // yes to a price): it must never render inside another site's
                // frame, where a clickjacking overlay could press it.
                source: '/q/:path*',
                headers: [
                    { key: 'X-Frame-Options', value: 'DENY' },
                    { key: 'Content-Security-Policy', value: "frame-ancestors 'none'" },
                ],
            },
        ];
    },
    async rewrites() {
        return [
            {
                // The single browser -> API path. Set-Cookie from FastAPI passes
                // back through this proxy, so the session cookie lands on the web
                // origin. SSE streams and large uploads deliberately do NOT use
                // this path: they go straight to NEXT_PUBLIC_API_URL with a 60s
                // HMAC ticket (see app/lib/api.ts), because a long-lived stream
                // should not hang off a serverless proxy hop.
                source: '/api/clefs/:path*',
                destination: `${CLEFS_API_ORIGIN}/:path*`,
            },
        ];
    },
    images: {
        remotePatterns: [
            {
                protocol: 'https',
                hostname: 'images.unsplash.com',
            },
        ],
    },
};

export default function config(phase) {
    warnIfRewriteBakedElsewhere(phase);
    return nextConfig;
}
