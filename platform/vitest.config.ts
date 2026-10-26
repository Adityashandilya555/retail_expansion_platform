import { defineConfig } from "vitest/config";

// One vitest run for every TS project in the workspace; coverage lands in platform/coverage/lcov.info.
// apps/api, apps/worker, packages/compiler and packages/schema are Python (uv workspace).
const projects = [
  "apps/web",
  "apps/console",
  "packages/zm-ui",
  "packages/zm-forms",
  "packages/zm-blocks",
  "packages/api-client",
];

export default defineConfig({
  test: {
    projects,
    passWithNoTests: true,
    coverage: {
      provider: "v8",
      reporter: ["text-summary", "lcov"],
      // zm-blocks keeps its blocks and layouts beside src/
      include: projects.map((p) => `${p}/{src,blocks,layouts}/**/*.{ts,tsx}`),
      // Vite mount boilerplate, dev-only previews (/dev/ui) and test helpers; also excluded in sonar-project.properties.
      exclude: ["**/src/main.tsx", "**/src/dev/**", "**/src/test/**"],
    },
  },
});
