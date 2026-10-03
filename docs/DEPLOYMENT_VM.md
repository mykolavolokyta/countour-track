# Deploy Contour on a virtual machine

This guide deploys a fresh, multi-user installation using the standalone `compose.vm.yaml` stack: Caddy, Next.js, FastAPI, a worker, and PostgreSQL. There is no migration from earlier development databases.

## 1. Prepare the host

Use a Linux VM with Docker Engine and the Compose plugin. A starting allocation of 2 vCPUs and 4 GiB RAM provides room to build the frontend on the host; adjust after measuring workload. Reserve persistent disk space for PostgreSQL, container images, certificates, and backups.

- Install Docker using the [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/) or the guide for your distribution.
- Ensure the [Compose plugin](https://docs.docker.com/compose/install/linux/) is installed.
- Install Git and Python 3 for checkout and configuration generation.
- Keep system time synchronized; OAuth and exchange signatures depend on it.
- Permit your configured SSH port and TCP 80/443 in the host and cloud firewalls. UDP 443 is optional for HTTP/3.
- Point a domain's DNS A record to the VM. Add an AAAA record only if IPv6 is correctly configured.

The stack publishes no API, frontend, or database ports directly. Caddy is the only public ingress.

## 2. Install the project

Clone your repository or upload a release to a directory such as `/opt/contour`:

```sh
git clone <repository-url> contour
cd contour
python3 scripts/setup.py
```

The setup script generates `.env` with mode `0600`. It refuses to overwrite an existing file and prints no secrets.

Edit `.env`:

```dotenv
APP_DOMAIN=portfolio.example.com
APP_ORIGIN=https://portfolio.example.com
ALLOW_REGISTRATION=true
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
TELEGRAM_CLIENT_ID=your-telegram-client-id
TELEGRAM_CLIENT_SECRET=your-telegram-client-secret
```

Replace the example domain with your own. Keep the generated `ENCRYPTION_KEY` and `POSTGRES_PASSWORD`. Never commit this file. Configure at least one sign-in provider.

## 3. Register production callbacks

Follow [Authentication](AUTH.md) and register:

```text
https://portfolio.example.com/api/auth/google/callback
https://portfolio.example.com/api/auth/telegram/callback
```

Google's client must be a Web application; add test users if its consent configuration is still in Testing mode. Telegram uses the Client ID/Secret from BotFather's Login Widget settings and RS256 signatures.

Keep registration enabled until the intended users have created accounts. There is no default owner password. Users who want both login methods must link the second from Settings.

## 4. Validate and start

Use the VM file by itself. Do not combine it with the local Compose file:

```sh
docker compose -f compose.vm.yaml config --quiet
docker compose -f compose.vm.yaml up --build -d
docker compose -f compose.vm.yaml ps
```

Avoid printing the fully expanded Compose configuration: it contains secrets. The API creates the schema before the worker starts.

Caddy requests and renews certificates automatically when DNS resolves correctly and ports 80/443 are reachable. See [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).

The VM network uses `172.30.90.0/24`; Caddy has `172.30.90.10`, which is the only forwarded-IP source trusted by the API. If this subnet conflicts with your environment, change the subnet, Caddy IP, and API `FORWARDED_ALLOW_IPS` together in `compose.vm.yaml`.

To validate the Caddy configuration after startup:

```sh
docker compose -f compose.vm.yaml exec -T proxy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

## 5. Verify the deployment

```sh
curl --fail https://portfolio.example.com/api/health
docker compose -f compose.vm.yaml logs --tail=100 api worker proxy
```

Then verify in the browser:

1. HTTPS loads without certificate errors.
2. Sign-in creates an empty portfolio.
3. Adding a manual USD asset updates the portfolio immediately.
4. A second user cannot see that asset or the first user's settings.
5. A connected read-only source completes a worker update.
6. Linking a second provider opens the same portfolio on the next login.
7. Sign out and Sign out on all devices invalidate the intended sessions.

Do not share callback URLs containing authorization codes or unredacted service logs. Caddy access logging is disabled; Uvicorn removes OAuth query parameters from access logs.

## 6. Back up data and secrets

Persisted state lives in the PostgreSQL volume; certificates live in Caddy volumes. Container replacement does not remove these volumes. Keep off-host backups and the matching encryption key.

Create a database dump from the project directory:

```sh
mkdir -p backups
chmod 700 backups
umask 077
docker compose -f compose.vm.yaml exec -T db pg_dump -U contour -d contour -Fc > backups/contour.dump
```

Check that the command succeeds before copying the dump off-host. Use dated or versioned names in an automated backup job so a failed run does not overwrite the only valid copy. Store `.env` or its secret values separately in secure storage. Without the original encryption key, restored provider credentials cannot be decrypted.

### Restore into a replacement deployment

Install the same application version and matching secrets first. Keep the application stopped while restoring:

```sh
docker compose -f compose.vm.yaml stop proxy web api worker
docker compose -f compose.vm.yaml up -d db
docker compose -f compose.vm.yaml exec -T db pg_isready -U contour -d contour
```

Wait for PostgreSQL to accept connections. The following command **replaces the target database contents** with the dump:

```sh
docker compose -f compose.vm.yaml exec -T db pg_restore -U contour -d contour --clean --if-exists --no-owner --exit-on-error < backups/contour.dump
docker compose -f compose.vm.yaml up -d
```

Only restart the application after a successful restore. Test this procedure on a separate deployment before relying on backups.

## 7. Update the application

Take a database backup and retain the previous release. Read release notes for schema changes; this release does not include a legacy migration system.

Install the intended new checkout/release, then:

```sh
docker compose -f compose.vm.yaml build api worker web
docker compose -f compose.vm.yaml up -d
docker compose -f compose.vm.yaml ps
```

The commands preserve data volumes. Do not use `down -v` for routine updates. Roll back application code only when the database format remains compatible; otherwise restore the corresponding backup too.

For environment changes:

```sh
docker compose -f compose.vm.yaml up -d --force-recreate api worker proxy
```

Changing the public domain also requires updating OAuth callbacks. To close registration after onboarding, set `ALLOW_REGISTRATION=false` and recreate the backend services.

## Troubleshooting

| Symptom | Checks |
| --- | --- |
| Certificate cannot be issued | DNS, stale AAAA records, firewall, ports 80/443, proxy logs |
| Sign-in fails | Exact HTTPS origin and callbacks, client secrets, clock, Google test users, Telegram RS256 |
| Sources stay queued | Worker is running; inspect worker logs and API health |
| Database rejects authentication | Existing volume credentials must match `.env`; changing the variable does not change an existing database password |
| Unsupported database schema | Use a fresh database for this release; older development databases are not imported |
| Widespread login rate limits | Preserve direct Caddy-to-API routing and the trusted proxy IP |
| Provider credentials cannot decrypt | Restore the original `ENCRYPTION_KEY`; do not regenerate it |

For modest workloads, one API and one worker are sufficient as an initial topology. Monitor synchronization lag, database growth, memory, and provider quotas before scaling. Multiple users are supported; unlimited throughput is not implied.
