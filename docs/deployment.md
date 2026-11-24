# Deployment — single VPS (Hostinger KVM, Ubuntu 24.04)

One server runs everything with Docker Compose: **Caddy** (TLS, routing) → **api** (FastAPI)
+ **worker** (background jobs) + **realtime** (WebSockets) + **Redis**, with SQLite on a volume
(or PostgreSQL with `--profile postgres`). Sized for a few businesses with up to 30 users each:
2 vCPU / 8 GB RAM is comfortable; 1 vCPU / 4 GB works.

> **Not yet validated on a real server.** The images, Compose file and Caddyfile are built and
> checked in CI (`deploy` job), but the first real deployment should follow this guide step by
> step and finish with the checks in §8.

## 1. Server preparation

```bash
# as root, once
adduser erp && usermod -aG sudo erp
# SSH keys only
mkdir -p /home/erp/.ssh && cp ~/.ssh/authorized_keys /home/erp/.ssh/ && chown -R erp:erp /home/erp/.ssh
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh

apt update && apt -y upgrade
apt -y install ufw fail2ban unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

ufw default deny incoming && ufw default allow outgoing
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp
ufw enable
```

Docker publishes ports itself; the only published ports are Caddy's 80/443. Redis, PostgreSQL,
the API and the real-time service have **no published ports** and sit on an internal network.

Install Docker Engine from Docker's apt repository (docs.docker.com → Install → Ubuntu), then
`usermod -aG docker erp`. Limit container log size in `/etc/docker/daemon.json`:

```json
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" } }
```

## 2. DNS (and Cloudflare)

Create `A` (and `AAAA` if the VPS has IPv6) records for the primary host, e.g.
`erp.example.com`, pointing at the VPS.

With Cloudflare in front: set SSL/TLS mode to **Full (strict)** (never "Flexible" — the ERP
forces HTTPS and Flexible causes a redirect loop and unencrypted origin traffic). Caddy still
obtains a real certificate via HTTP-01, which Cloudflare passes through. Disable "Always Use
HTTPS" rewrites only if they conflict; Caddy already redirects.

## 3. Configuration

```bash
git clone <your repository> /opt/erp && cd /opt/erp/deploy
cp .env.example .env && chmod 600 .env
```

Fill in every value in `.env` (each line says how to generate it):

| Setting | Notes |
|---|---|
| `ERP_PRIMARY_HOST`, `ERP_ALLOWED_HOSTS`, `ERP_PUBLIC_BASE_URL` | the primary host name |
| `ERP_SECRET_KEY` | signs sessions; changing it signs everyone out |
| `ERP_ENCRYPTION_KEYS` | encrypts integration credentials and 2FA secrets |
| `ERP_BACKUP_KEY` | encrypts backups — **store a copy off the server** (password manager + paper). Without it no backup can be restored. |
| `ERP_TLS_ASK_TOKEN`, `ERP_METRICS_TOKEN` | random tokens |
| `ERP_SMTP_*` | optional: security alert e-mails to users |

Check them: `docker compose run --rm api python -m app.cli check-config`.

## 4. Start

```bash
docker compose up -d --build        # SQLite
# or: docker compose --profile postgres up -d --build
docker compose ps                   # all healthy; "migrate" exited 0
docker compose exec api python -m app.cli create-owner --email you@example.com --name "Your Name"
```

Sign in, then **turn on two-factor authentication for the owner account first**
(`POST /api/v1/auth/2fa/setup` → `/2fa/enable`; the frontend will offer this on the profile
page). Store the recovery codes offline.

## 5. Custom domains

1. Owner: `POST /api/v1/system/domains {"hostname": "billing.myclinic.in"}`.
2. At the domain's DNS: an `A`/`CNAME` record to the server **and** the TXT record returned in
   `dns_record` (`_erp-verify.billing.myclinic.in  TXT  erp-verify=…`).
3. `POST /api/v1/system/domains/{id}/verify`. Once `active`, Caddy obtains a certificate on
   the first visit. The worker re-checks the TXT record daily and disables the domain after
   three failed checks; keep the record in place.

A custom domain changes nothing about access: users still sign in and see only the businesses
and branches they are assigned to.

## 6. Backups and restore

* The worker takes an encrypted, verified backup daily at `hour_utc` (default 20:00 UTC =
  01:30 IST) into the `erp-data` volume (`/app/data/backups`) and prunes them
  grandfather-father-son (7 daily, 4 weekly, 12 monthly by default):
  `GET/PUT /api/v1/system/backups/settings`.
* **Off-site copy**: connect a Google integration in any business and set
  `offsite_integration_id`; each backup is uploaded to Drive (encrypted). Also download one
  regularly: `GET /api/v1/system/backups/{id}/download`.
* On demand: `POST /api/v1/system/backups`, or `docker compose exec api python -m app.cli backup`.
* Check a file anywhere: `python -m app.cli verify-backup <file>` (needs `ERP_BACKUP_KEY`).

**Restore** (stop writers first):

```bash
docker compose stop api worker realtime
docker compose run --rm api python -m app.cli restore /app/data/backups/<file>.erpbk --force
docker compose run --rm api python -m app.cli migrate
docker compose up -d
```

**Restore drill — do it monthly**: restore the latest backup into a scratch target and check it:

```bash
docker compose run --rm api sh -c '
  python -m app.cli restore /app/data/backups/$(ls -t /app/data/backups | head -1) \
    --target sqlite:////tmp/drill.db --yes &&
  ERP_DATABASE_URL=sqlite:////tmp/drill.db python -m app.cli db-check &&
  ERP_DATABASE_URL=sqlite:////tmp/drill.db python -m app.cli accounting-backfill'
```

(`accounting-backfill` is idempotent and re-verifies every business's books.) The automated
test suite runs the same drill on SQLite and PostgreSQL in CI.

## 7. Monitoring

* `GET /api/v1/system/health` (owner): warnings in plain language — worker stopped, backup
  missing or failed, queues stuck, disk, disabled domains, unsafe production settings.
* `GET /api/v1/ready`: used by the container health checks.
* Prometheus: scrape `http://api:8000/api/v1/metrics` from inside the Docker network with
  `Authorization: Bearer $ERP_METRICS_TOKEN` (Caddy refuses `/metrics` from outside). Alert on
  `erp_worker_heartbeat_age_seconds > 300`, `erp_backup_last_success_age_seconds > 93600`,
  `erp_queue_oldest_seconds > 900` and `erp_ready == 0`.
* A simple external uptime check on `https://<host>/api/v1/health` is enough to start.
* Logs: `docker compose logs -f api worker` (JSON lines; rotated by the Docker daemon setting
  above). Caddy access logs rotate at 50 MB × 10 in the `caddy-data` volume.

## 8. Go-live checklist

- [ ] `check-config` prints "Configuration OK for production"
- [ ] `https://<host>/api/v1/health` → 200 with HSTS header; `http://` redirects
- [ ] `curl -H "Host: random.example" https://<host>/api/v1/auth/me` → 421
- [ ] `https://<host>/api/v1/metrics` → 404 from outside
- [ ] owner has 2FA on; recovery codes stored offline
- [ ] first backup succeeded, downloaded and verified on another machine; backup key stored off-server
- [ ] restore drill (§6) completed
- [ ] `/system/health` status is `ok`

## 9. Updates

```bash
cd /opt/erp && git pull
cd deploy && docker compose exec api python -m app.cli backup   # safety copy
docker compose up -d --build                                    # migrations run first ("migrate")
```

## Why Caddy rather than Nginx

The specification lists Nginx. Caddy was chosen because custom domains need certificates to be
issued and renewed automatically *only* for verified domains: Caddy's on-demand TLS with an
`ask` endpoint does exactly that, with no certbot cron jobs or reload scripts. With Nginx the
same needs certbot plus a script that writes a server block per verified domain and reloads
Nginx. If you prefer Nginx anyway, the API side is unchanged (`/system/tls-ask` can drive the
certbot script); only `deploy/` changes.
