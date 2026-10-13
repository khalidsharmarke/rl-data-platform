import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser calls /api/*; the dev server forwards it to the FastAPI service.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.API_URL ?? "http://localhost:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
