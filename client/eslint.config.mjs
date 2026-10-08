import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

/* Flat config. `next lint` was removed in Next 16, so `npm run lint` runs the
   ESLint CLI directly. This mirrors the old .eslintrc.json
   ("next/core-web-vitals") so the rule set did not silently change in the move. */
const eslintConfig = defineConfig([
  ...nextVitals,
  {
    rules: {
      /* eslint-plugin-react-hooks v7 ships the React Compiler rules in its
         recommended set. They postdate the dashboard, concierge and marketing
         code (several thousand lines that set state from effects on purpose,
         e.g. syncing SSE pushes and ResizeObserver widths), and those files are
         owned by other lanes and partly frozen (avatar/viseme code). Kept as
         warnings so they stay visible without failing CI; new code should
         still heed them. */
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/purity": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/static-components": "warn",
      "react-hooks/use-memo": "warn",
      "react-hooks/preserve-manual-memoization": "warn",
      "react-hooks/incompatible-library": "warn",
      "react-hooks/globals": "warn",
      "react-hooks/error-boundaries": "warn",
      "react-hooks/unsupported-syntax": "warn",
      "react-hooks/config": "warn",
      "react-hooks/gating": "warn",
    },
  },
  {
    /* ClefsConcierge.tsx is frozen in the foundation wave (it carries the
       avatar/lip-sync code) and has one pre-existing <a href="/"> on its
       "hotel not found" screen. A full-page load there is harmless, so it is
       held at warning until the Concierge lane owns the file; FOUNDATION-REQUEST
       for that lane: swap it for next/link and delete this block. */
    files: ["app/ClefsConcierge.tsx"],
    rules: { "@next/next/no-html-link-for-pages": "warn" },
  },
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Build artefacts and third-party sources that are not ours to lint.
    "node_modules/**",
    "certificates/**",
    "avatar-source/**",
    "public/**",
  ]),
]);

export default eslintConfig;
