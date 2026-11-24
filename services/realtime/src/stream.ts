/**
 * Reads the ERP event stream (Redis Stream written by the Python outbox relay).
 *
 * Every realtime instance reads every entry (plain XREAD, not a consumer group): each instance
 * serves its own sockets, so all of them need the full feed. Replay for reconnecting clients
 * uses XRANGE from the client's last stream id.
 */

import type { Redis } from "ioredis";

import type { ErpEvent } from "./permissions.js";

const MAX_REPLAY_SCAN = 5000;

export function toEvent(fields: string[]): ErpEvent {
  const f: Record<string, string> = {};
  for (let i = 0; i + 1 < fields.length; i += 2) f[fields[i]!] = fields[i + 1]!;
  return { id: f["id"] ?? "", type: f["type"] ?? "", business_id: f["business_id"] ?? "",
           created_at: f["created_at"] ?? "", data: f["data"] ? JSON.parse(f["data"]) : {} };
}

function cmp(a: string, b: string): number {
  const [am, as] = a.split("-").map(BigInt);
  const [bm, bs] = b.split("-").map(BigInt);
  if (am! !== bm!) return am! < bm! ? -1 : 1;
  return as! === bs! ? 0 : as! < bs! ? -1 : 1;
}

const STREAM_ID = /^\d+-\d+$/;

export async function replay(redis: Redis, stream: string, businessId: string, afterId: string):
    Promise<{ events: [string, ErpEvent][]; complete: boolean }> {
  if (!STREAM_ID.test(afterId)) return { events: [], complete: false };
  const first = await redis.xrange(stream, "-", "+", "COUNT", 1);
  if (first.length && cmp(afterId, first[0]![0]) < 0) {
    return { events: [], complete: false }; // trimmed past the client's cursor: it must resync
  }
  const rows = await redis.xrange(stream, `(${afterId}`, "+", "COUNT", MAX_REPLAY_SCAN + 1);
  if (rows.length > MAX_REPLAY_SCAN) return { events: [], complete: false };
  const events: [string, ErpEvent][] = [];
  for (const [id, fields] of rows) {
    const ev = toEvent(fields);
    if (ev.business_id === businessId) events.push([id, ev]);
  }
  return { events, complete: true };
}

export async function follow(redis: Redis, stream: string, onEvent: (id: string, ev: ErpEvent) => void,
                             shouldStop: () => boolean, log: (m: string) => void): Promise<void> {
  let lastId = "$"; // only new entries; reconnecting clients use replay for the past
  while (!shouldStop()) {
    try {
      const res = (await redis.xread("COUNT", 500, "BLOCK", 5000, "STREAMS", stream, lastId)) as
        [string, [string, string[]][]][] | null;
      if (!res) continue;
      for (const [, entries] of res) {
        for (const [id, fields] of entries) {
          lastId = id;
          try {
            onEvent(id, toEvent(fields));
          } catch (err) {
            log(`bad stream entry ${id}: ${String(err)}`);
          }
        }
      }
    } catch (err) {
      if (shouldStop()) return;
      log(`stream read failed: ${String(err)}; retrying`);
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
}
