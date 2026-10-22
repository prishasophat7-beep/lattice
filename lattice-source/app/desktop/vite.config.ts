import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed dev port; the backend's CORS list allows http://localhost:1420.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: "localhost",
    // Rust's build output under src-tauri/target is huge and locked while compiling;
    // watching it crashes Vite on Windows (EBUSY).
    watch: { ignored: ["**/src-tauri/**"] },
  },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  build: { target: "es2022", sourcemap: false, outDir: "dist" },
});
