import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

export default defineConfig({
  plugins: [preact()],
  build: {
    outDir: process.env.CONSTELLA_FRONTEND_OUT_DIR || "dist",
    emptyOutDir: true,
  },
});
