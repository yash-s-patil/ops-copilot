import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const at = (path: string) => fileURLToPath(new URL(path, import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": at("./src"),
      // The real package throws when it is loaded outside a server bundle.
      "server-only": at("./src/test/empty.ts"),
    },
  },
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    setupFiles: ["./src/test/setup.ts"],
    unstubGlobals: true,
    unstubEnvs: true,
    restoreMocks: true,
  },
});
