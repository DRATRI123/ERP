# Real-time service (Node.js)

Delivers ERP events to browsers and apps over WebSockets: new invoices, payments, stock changes,
dashboard refreshes, presence (who is online and what they are viewing).

```
Python API ── commit ──► outbox table ── worker relay ──► Redis Stream "erp:events" ──► this service ──► WebSocket clients
```

* **Auth is delegated to the Python API** (`GET /api/v1/auth/me`). Sessions are re-checked every
  60 s, so logout, suspension and permission changes end live connections too.
* **Filtering**: per business, per permission (deny by default for unknown event types), and per
  branch for branch-limited users. Presence is shown only to users with `users.view`.
* **Resume**: clients send their last `stream_id`; missed events are replayed from the stream
  (or `resync_required` if the gap is too large). Delivery is at-least-once: de-duplicate on
  `event.id`.
* Heartbeats (ping/pong), slow-consumer protection, message flood limits, Origin allow-list,
  graceful restart (close code 1012 → clients reconnect and resume).

## Run

```bash
npm ci && npm run build
ERP_API_URL=http://127.0.0.1:8000 ERP_REDIS_URL=redis://127.0.0.1:6379/0 \
RT_ALLOWED_ORIGINS=https://erp.example.com npm start      # listens on :8090, WebSocket at /ws
```

The Python side needs `ERP_REDIS_URL` and `python -m app.cli worker` running (it relays the outbox).

## Protocol (JSON frames)

| client → server | server → client |
|---|---|
| `{"type":"auth","token":"<access token>"}` — first message; resend to refresh | `{"type":"ready","user","businesses"}` |
| `{"type":"subscribe","business_id","last_event_id?"}` | `{"type":"subscribed","business_id","replayed"}` |
| `{"type":"unsubscribe","business_id"}` | `{"type":"event","stream_id","event":{id,type,business_id,created_at,data}}` |
| `{"type":"activity","business_id","viewing"}` | `{"type":"presence","business_id","users"}` |
| `{"type":"ping"}` | `{"type":"pong"}`, `{"type":"resync_required"}`, `{"type":"session_ended"}`, `{"type":"error","code","message"}` |

Tokens go in the first message, never the URL (URLs end up in proxy logs).

## Tests

`npm test` (unit). The full stack (API + worker + Redis + this service + WebSocket clients) is
exercised by `backend/tests/test_realtime_e2e.py`.

## Why webhook delivery stays in Python

Webhook delivery needs the encrypted signing secrets and the delivery tables owned by the Python
backend, and it is already retried and tested there. Moving it here would duplicate that logic
without a measured benefit; it can move if volume ever requires.
