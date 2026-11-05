import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // focus.css is read as text by theme.test.ts (vitest blanks CSS otherwise)
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"], css: { include: [/focus\.css/] } },
});
