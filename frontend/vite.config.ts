import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built straight into the static dir FastAPI already serves, so deployment stays "copy
// the wheel" — no second server, no node on the box. `base` matches the mount path
// because the app is served from /static/app, not from the domain root.
export default defineConfig({
  plugins: [react()],
  base: "/static/app/",
  build: {
    outDir: "../src/api/static/app",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // Dev only. Everything that is not the SPA itself is the FastAPI app: the JSON it
    // reads, the form posts it still uses, and console.css — which is NOT bundled, so
    // the React screens and the Jinja screens keep sharing one stylesheet.
    proxy: Object.fromEntries(
      ["/api", "/static", "/messages", "/customers", "/pipeline", "/email-templates",
       "/policy-docs", "/operations", "/logs", "/settings", "/auth", "/contacts",
       "/companies", "/tools", "/outbound-history", "/integrations"].map((path) => [
        path,
        { target: "http://127.0.0.1:8010", changeOrigin: true },
      ]),
    ),
  },
});
