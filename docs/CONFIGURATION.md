# Configuration

Native development loads the repository-root `.env`. Explicit process variables take precedence. Docker Compose supplies the backend environment directly.

```sh
python3 scripts/setup.py
```

Setup uses the Python standard library, generates encryption and database secrets, writes mode `0600`, and refuses to overwrite an existing file. To generate a separate file:

```sh
python3 scripts/setup.py --output .env.new
```

Only `.env` loads automatically. Select another file explicitly with Compose or install it as `.env` before native startup.

## Variables

| Variable | Purpose / default |
| --- | --- |
| `APP_ORIGIN` | Exact browser origin; default `http://localhost:3000` |
| `APP_DOMAIN` | Caddy hostname for the VM stack |
| `ENCRYPTION_KEY` | Fernet key for source credentials and OAuth transaction data |
| `POSTGRES_PASSWORD` | Docker database password; generated as URL-safe hex |
| `DATABASE_URL` | Native database URL; defaults to project-root `contour.db` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google OIDC credentials |
| `TELEGRAM_CLIENT_ID`, `TELEGRAM_CLIENT_SECRET` | Telegram Login OIDC credentials |
| `ALLOW_REGISTRATION` | `true` by default; `false` prevents new accounts |
| `BSC_RPC_URLS` | Comma-separated HTTPS RPC endpoints; up to three used for fallback |
| `COINGECKO_DEMO_API_KEY` | Optional CoinGecko Demo key |
| `MORALIS_API_KEY` | Optional BSC token-discovery provider |
| `FORWARDED_ALLOW_IPS` | Native/local API trusted proxy IPs; default `127.0.0.1` |

The VM stack fixes the trusted proxy to Caddy's private IP. Do not replace it with `*` on an exposed API. Both Docker stacks construct `DATABASE_URL` internally; `.env` changes to this variable affect native development only. Manually selected database passwords must be URL-safe or correctly encoded in the connection URL.

## Database initialization

The API creates its schema in an empty database and creates no users. Only verified social sign-in creates a user. This fresh-install release has no conversion path for earlier schemas; startup rejects incompatible databases without deleting them.

Native examples:

```dotenv
DATABASE_URL=sqlite:////absolute/path/to/contour.db
```

```dotenv
DATABASE_URL=postgresql+psycopg://contour:URL_SAFE_PASSWORD@127.0.0.1:5432/contour
```

SQLite is for local development; use PostgreSQL on the VM. Changing URLs does not copy data between databases.

## Runtime

Use Python 3.12+ with modern OpenSSL and Node.js 22+. The macOS system Python 3.9 can produce urllib3/LibreSSL warnings; use a current Python build or Docker.

Start the API before the worker. Native Next.js proxies `/api/*` to `http://127.0.0.1:8000`; the frontend Docker build uses `http://api:8000`. The VM routes API requests directly through Caddy so client IPs are preserved.

Backend configuration changes require a restart. For the local Docker stack:

```sh
docker compose up -d --force-recreate api worker
```

Synchronization intervals live in the database per user, with optional per-source overrides. The default is 60 minutes; the supported range is 5–1440 minutes.

## Secret storage

Keep `.env` out of version control. Back up the encryption key separately from the database; losing it makes stored exchange and bank credentials unreadable. Generating a new key is not an existing-key rotation procedure.

### EVM RPC endpoints

`ETHEREUM_RPC_URLS`, `ARBITRUM_RPC_URLS`, `OPTIMISM_RPC_URLS` and
`BASE_RPC_URLS` optionally override the bundled public endpoints. Like
`BSC_RPC_URLS`, each is a comma-separated list with up to three endpoints used
for fallback. Both Compose files pass these settings to the API and worker.
RPC URLs are server-controlled and cannot be supplied by portfolio users.
After changing settings, recreate the API and worker containers.
