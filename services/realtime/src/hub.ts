/**
 * Connections, business subscriptions, presence, and per-connection event filtering.
 *
 * Wire protocol (JSON text frames):
 *   client → server  {type:"auth", token}                      first message, and to refresh
 *                    {type:"subscribe", business_id, last_event_id?}   join a business feed
 *                    {type:"unsubscribe", business_id}
 *                    {type:"activity", business_id, viewing?}   presence detail (e.g. "INV/…/0042")
 *                    {type:"ping"}
 *   server → client  {type:"ready", user, businesses}
 *                    {type:"subscribed", business_id, replayed}
 *                    {type:"event", stream_id, event}           stream_id is the resume cursor
 *                    {type:"resync_required", business_id}      gap too large: reload data
 *                    {type:"presence", business_id, users}
 *                    {type:"pong"} | {type:"error", code, message} | {type:"session_ended"}
 */

import type { WebSocket } from "ws";

import { AuthError, type Me } from "./erp.js";
import { canSee, canSeePresence, type BusinessAccess, type ErpEvent } from "./permissions.js";

export interface HubDeps {
  validate(token: string): Promise<Me>;
  replay(businessId: string, afterId: string): Promise<{ events: [string, ErpEvent][]; complete: boolean }>;
  maxBuffered: number;
}

export class Client {
  me: Me | null = null;
  token = "";
  readonly subscriptions = new Set<string>();
  readonly viewing = new Map<string, string>();
  alive = true;
  private window: number[] = [];

  constructor(readonly ws: WebSocket, readonly id: string) {}

  access(businessId: string): BusinessAccess | undefined {
    return this.me?.businesses.find((b) => b.business_id === businessId);
  }

  send(msg: unknown, maxBuffered: number): void {
    if (this.ws.readyState !== this.ws.OPEN) return;
    if (this.ws.bufferedAmount > maxBuffered) {
      this.ws.terminate(); // a client that cannot keep up is dropped; it will reconnect & resume
      return;
    }
    this.ws.send(JSON.stringify(msg));
  }

  /** Simple flood guard: at most `limit` messages per 10 s. */
  allowMessage(limit = 60): boolean {
    const now = Date.now();
    this.window = this.window.filter((t) => t > now - 10_000);
    this.window.push(now);
    return this.window.length <= limit;
  }
}

export class Hub {
  readonly clients = new Set<Client>();
  private readonly byBusiness = new Map<string, Set<Client>>();
  delivered = 0;

  constructor(private readonly deps: HubDeps) {}

  add(client: Client): void {
    this.clients.add(client);
  }

  remove(client: Client): void {
    this.clients.delete(client);
    for (const bid of client.subscriptions) this.leave(client, bid);
  }

  /** Fan one stream entry out to every subscriber allowed to see it. */
  dispatch(streamId: string, event: ErpEvent): void {
    const subs = this.byBusiness.get(event.business_id);
    if (!subs) return;
    for (const c of subs) {
      const access = c.access(event.business_id);
      if (access && canSee(access, event)) {
        c.send({ type: "event", stream_id: streamId, event }, this.deps.maxBuffered);
        this.delivered++;
      }
    }
  }

  async handle(client: Client, raw: string): Promise<void> {
    if (!client.allowMessage()) {
      client.send({ type: "error", code: "rate_limited", message: "Too many messages" }, this.deps.maxBuffered);
      client.ws.close(4429, "rate limited");
      return;
    }
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return this.error(client, "bad_message", "Messages must be JSON");
    }
    const type = msg["type"];
    if (type === "auth") return this.auth(client, String(msg["token"] ?? ""));
    if (!client.me) return this.error(client, "not_authenticated", "Send {type:'auth'} first");
    const bid = String(msg["business_id"] ?? "");
    switch (type) {
      case "subscribe":
        return this.subscribe(client, bid, msg["last_event_id"] ? String(msg["last_event_id"]) : null);
      case "unsubscribe":
        this.leave(client, bid);
        return;
      case "activity":
        if (!client.subscriptions.has(bid)) return;
        client.viewing.set(bid, String(msg["viewing"] ?? "").slice(0, 100));
        this.broadcastPresence(bid);
        return;
      case "ping":
        client.send({ type: "pong" }, this.deps.maxBuffered);
        return;
      default:
        return this.error(client, "unknown_type", `Unknown message type ${String(type)}`);
    }
  }

  private async auth(client: Client, token: string): Promise<void> {
    try {
      const me = await this.deps.validate(token);
      if (client.me && client.me.user.id !== me.user.id) {
        return this.error(client, "user_mismatch", "A connection cannot switch users");
      }
      client.me = me;
      client.token = token;
      this.prune(client);
      client.send({ type: "ready", user: me.user, businesses: me.businesses.map((b) => ({
        business_id: b.business_id, business_name: b.business_name, role: b.role })) }, this.deps.maxBuffered);
    } catch (err) {
      if (err instanceof AuthError) {
        client.send({ type: "session_ended" }, this.deps.maxBuffered);
        client.ws.close(4401, "unauthorised");
      } else {
        this.error(client, "auth_unavailable", "Could not verify the session; retry shortly");
      }
    }
  }

  private async subscribe(client: Client, bid: string, lastEventId: string | null): Promise<void> {
    if (!client.access(bid)) return this.error(client, "forbidden", "No access to this business");
    let replayed = 0;
    if (lastEventId) {
      // Join first so nothing published during the replay is missed (duplicates are possible
      // and harmless: clients de-duplicate on event.id).
      this.join(client, bid);
      const { events, complete } = await this.deps.replay(bid, lastEventId);
      if (!complete) {
        client.send({ type: "resync_required", business_id: bid }, this.deps.maxBuffered);
      } else {
        const access = client.access(bid);
        for (const [sid, ev] of events) {
          if (access && canSee(access, ev)) {
            client.send({ type: "event", stream_id: sid, event: ev }, this.deps.maxBuffered);
            replayed++;
          }
        }
      }
    } else {
      this.join(client, bid);
    }
    client.send({ type: "subscribed", business_id: bid, replayed }, this.deps.maxBuffered);
    this.broadcastPresence(bid);
  }

  private join(client: Client, bid: string): void {
    client.subscriptions.add(bid);
    let set = this.byBusiness.get(bid);
    if (!set) this.byBusiness.set(bid, (set = new Set()));
    set.add(client);
  }

  private leave(client: Client, bid: string): void {
    client.subscriptions.delete(bid);
    client.viewing.delete(bid);
    const set = this.byBusiness.get(bid);
    if (!set) return;
    set.delete(client);
    if (set.size === 0) this.byBusiness.delete(bid);
    this.broadcastPresence(bid);
  }

  /** Drop subscriptions the user no longer has access to (after re-validation). */
  prune(client: Client): void {
    for (const bid of [...client.subscriptions]) {
      if (!client.access(bid)) this.leave(client, bid);
    }
  }

  presence(bid: string): { user_id: string; name: string; connections: number; viewing: string[] }[] {
    const users = new Map<string, { user_id: string; name: string; connections: number; viewing: string[] }>();
    for (const c of this.byBusiness.get(bid) ?? []) {
      if (!c.me) continue;
      const u = users.get(c.me.user.id) ?? { user_id: c.me.user.id, name: c.me.user.full_name, connections: 0, viewing: [] };
      u.connections++;
      const v = c.viewing.get(bid);
      if (v) u.viewing.push(v);
      users.set(u.user_id, u);
    }
    return [...users.values()];
  }

  private broadcastPresence(bid: string): void {
    const users = this.presence(bid);
    for (const c of this.byBusiness.get(bid) ?? []) {
      const access = c.access(bid);
      if (access && canSeePresence(access)) {
        c.send({ type: "presence", business_id: bid, users }, this.deps.maxBuffered);
      }
    }
  }

  /** Re-check every connection's session; end the ones the API no longer accepts. */
  async revalidateAll(): Promise<void> {
    await Promise.all([...this.clients].filter((c) => c.me).map(async (c) => {
      try {
        c.me = await this.deps.validate(c.token);
        this.prune(c);
      } catch (err) {
        if (err instanceof AuthError) {
          c.send({ type: "session_ended" }, this.deps.maxBuffered);
          c.ws.close(4401, "session ended");
        } // API unreachable: keep the connection; try again next round
      }
    }));
  }

  private error(client: Client, code: string, message: string): void {
    client.send({ type: "error", code, message }, this.deps.maxBuffered);
  }
}
