// Production Node.js server: serves the built portal and proxies /api to the Python backend.
import express from "express";
import { createProxyMiddleware } from "http-proxy-middleware";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// CLI flags win over env vars: node server.js --port 5181 --api http://127.0.0.1:8030
const arg = (name) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : undefined;
};
const PORT = Number(arg("port") || process.env.PORT || 3000);
const API_URL = arg("api") || process.env.API_URL || "http://127.0.0.1:8000";
const dist = path.join(__dirname, "dist");

const app = express();
app.disable("x-powered-by");
app.use((_, res, next) => {
  res.set({
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
  });
  next();
});
app.use(
  "/api",
  createProxyMiddleware({
    target: `${API_URL}/api`,
    changeOrigin: true,
    on: {
      // API down or restarting: answer with JSON the portal can show, instead of hanging.
      error: (err, _req, res) => {
        console.error(`[proxy] ${err.code || err.message}`);
        if (res && !res.headersSent && typeof res.writeHead === "function") {
          res.writeHead(502, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ detail: "The Docs Health API is not reachable. It may be restarting." }));
        }
      },
    },
  }),
);
app.use(express.static(dist, { index: false, maxAge: "1h" }));
app.get("*", (_, res) => res.sendFile(path.join(dist, "index.html")));

const server = app.listen(PORT, () => console.log(`Docs Health portal on http://localhost:${PORT} (API -> ${API_URL})`));
server.on("error", (err) => {
  console.error(err.code === "EADDRINUSE" ? `Port ${PORT} is already in use.` : err);
  process.exit(1);
});
