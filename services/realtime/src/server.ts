import { randomUUID } from "node:crypto";
import http from "node:http";

import { Redis } from "ioredis";
import { WebSocketServer } from "ws";

import { fetchMe } from "./erp.js";
import { Client, Hub } from "./hub.js";
import { follow, replay } from "./stream.js";

export interface Config {
  port: number;
  host: string;
  apiUrl: string;
  redisUrl: string;
  stream: string;
  allowedOrigins: string[]; // empty = allow any (non-browser clients send no Origin)
  revalidateMs: number;
  heartbeatMs: number;
  authTimeoutMs: number;
  maxBuffered: number;
}

export function configFromEnv(env = process.env): Config {
  return {
    port: Number(env["RT_PORT"] ?? 8090),
    host: env["RT_HOST"] ?? "0.0.0.0",
    apiUrl: env["ERP_API_URL"] ?? "http://127.0.0.1:8000",
    redisUrl: env["ERP_REDIS_URL"] ?? "redis://127.0.0.1:6379/0",
    stream: env["ERP_EVENT_STREAM"] ?? "erp:events",
    allowedOrigins: (env["RT_ALLOWED_ORIGINS"] ?? "").split(",").map((s) => s.trim()).filter(Boolean),
    revalidateMs: Number(env["RT_REVALIDATE_MS"] ?? 60_000),
    heartbeatMs: Number(env["RT_HEARTBEAT_MS"] ?? 30_000),
    authTimeoutMs: Number(env["RT_AUTH_TIMEOUT_MS"] ?? 10_000),
    maxBuffered: Number(env["RT_MAX_BUFFERED_BYTES"] ?? 1_000_000),
  };
}

const log = (msg: string) => console.log(JSON.stringify({ ts: new Date().toISOString(), svc: "realtime", msg }));

export async function start(cfg: Config): Promise<{ close: () => Promise<void>; port: number; hub: Hub }> {
  const reader = new Redis(cfg.redisUrl, { maxRetriesPerRequest: null, lazyConnect: false });
  const commands = new Redis(cfg.redisUrl, { maxRetriesPerRequest: 2 });
  const hub = new Hub({
    validate: (token) => fetchMe(cfg.apiUrl, token),
    replay: (bid, after) => replay(commands, cfg.stream, bid, after),
    maxBuffered: cfg.maxBuffered,
  });
  let stopping = false;

  const server = http.createServer(async (req, res) => {
    if (req.url === "/health") {
      let redisOk = true;
      try { await commands.ping(); } catch { redisOk = false; }
      res.writeHead(redisOk ? 200 : 503, { "content-type": "application/json" });
      res.end(JSON.stringify({ status: redisOk ? "ok" : "degraded", redis: redisOk,
                               connections: hub.clients.size, delivered: hub.delivered }));
      return;
    }
    res.writeHead(404).end();
  });

  const wss = new WebSocketServer({ noServer: true, maxPayload: 64 * 1024 });
  server.on("upgrade", (req, socket, head) => {
    const origin = req.headers.origin;
    if (req.url?.split("?")[0] !== "/ws" ||
        (origin && cfg.allowedOrigins.length && !cfg.allowedOrigins.includes(origin))) {
      socket.write("HTTP/1.1 403 Forbidden\r\n\r\n");
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req));
  });

  wss.on("connection", (ws) => {
    const client = new Client(ws, randomUUID());
    hub.add(client);
    // The token travels in the first message, never in the URL (URLs end up in logs).
    const authTimer = setTimeout(() => { if (!client.me) ws.close(4401, "auth timeout"); }, cfg.authTimeoutMs);
    ws.on("pong", () => { client.alive = true; });
    ws.on("message", (data) => { void hub.handle(client, data.toString()); });
    ws.on("close", () => { clearTimeout(authTimer); hub.remove(client); });
    ws.on("error", () => ws.terminate());
  });

  const heartbeat = setInterval(() => {
    for (const c of hub.clients) {
      if (!c.alive) { c.ws.terminate(); continue; }
      c.alive = false;
      c.ws.ping();
    }
  }, cfg.heartbeatMs);
  const revalidate = setInterval(() => { void hub.revalidateAll(); }, cfg.revalidateMs);
  const following = follow(reader, cfg.stream, (id, ev) => hub.dispatch(id, ev), () => stopping, log);

  await new Promise<void>((resolve) => server.listen(cfg.port, cfg.host, resolve));
  const port = (server.address() as { port: number }).port;
  log(`listening on ${cfg.host}:${port}`);

  return {
    port,
    hub,
    close: async () => {
      stopping = true;
      clearInterval(heartbeat);
      clearInterval(revalidate);
      for (const c of hub.clients) c.ws.close(1012, "service restart"); // clients reconnect + resume
      await new Promise<void>((r) => wss.close(() => r()));
      await new Promise<void>((r) => server.close(() => r()));
      reader.disconnect();
      await following.catch(() => undefined);
      commands.disconnect();
    },
  };
}
