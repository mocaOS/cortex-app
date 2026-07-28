import type { ZudokuBuildConfig } from "zudoku";

// Prerender worker count defaults to 80% of the host's cores. On the shared
// deployment server that spawned 12 SSR workers and the build died (silent
// 6-minute stall in "prerendering 46 routes", exit 255 — memory pressure).
// 4 keeps the prerender well inside the build container's budget; the full
// prerender finishes in seconds either way.
const buildConfig: ZudokuBuildConfig = {
  prerender: {
    workers: 4,
  },
};

export default buildConfig;
