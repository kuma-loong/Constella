import { defineConfig } from "vite";
import preact from "@preact/preset-vite";
import { resolve } from "node:path";

const editionEntry = process.env.CONSTELLA_FRONTEND_MODE === "lab" ? "lab-main.tsx" : "main.tsx";

export default defineConfig({
  plugins: [preact()],
  resolve: {
    alias: {
      "@constella-edition-entry": resolve(import.meta.dirname, "src", editionEntry),
    },
  },
  build: {
    outDir: process.env.CONSTELLA_FRONTEND_OUT_DIR || "dist",
    emptyOutDir: true,
  },
});
