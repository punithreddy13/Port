// server.mjs — tiny zero-dependency web server for the live dashboard.
// Serves public/dashboard.html and exposes data/state.json at /api/state.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { SRC_DIR, BOT_DIR, DATA_DIR } from "./util.mjs";

const PORT = process.env.PORT || 4317;
const DASHBOARD = join(BOT_DIR, "public", "dashboard.html");
const STATE = join(DATA_DIR, "state.json");

const server = createServer(async (req, res) => {
  try {
    if (req.url === "/api/state") {
      const data = await readFile(STATE, "utf8").catch(
        () => JSON.stringify({ error: "No state yet. Run `npm start` or `npm run start -- --replay`." })
      );
      res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      res.end(data);
      return;
    }
    if (req.url === "/" || req.url === "/index.html") {
      const html = await readFile(DASHBOARD, "utf8");
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(html);
      return;
    }
    res.writeHead(404); res.end("not found");
  } catch (e) {
    res.writeHead(500); res.end(String(e.message));
  }
});

server.listen(PORT, () => {
  console.log(`Dashboard: http://localhost:${PORT}  (reading ${STATE})`);
});
