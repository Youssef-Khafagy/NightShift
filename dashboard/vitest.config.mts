import path from "node:path";

import { defineConfig } from "vitest/config";

// The same "@/..." import alias the app uses (tsconfig.json paths).
export default defineConfig({
  resolve: { alias: { "@": path.resolve(import.meta.dirname) } },
  test: { environment: "node" },
});
