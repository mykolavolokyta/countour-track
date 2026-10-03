# Contour

A self-hosted, multi-user portfolio tracker for exchange balances, on-chain wallets, bank accounts, and manually recorded assets.

Contour combines Binance, Bybit, BNB Smart Chain, and PrivatBank business accounts in one USD-denominated dashboard. Each user signs in with Google or Telegram and has an isolated portfolio, credentials, history, and synchronization preferences. The interface currently uses Ukrainian; project documentation is in English.

## Features

- **Independent portfolios:** sources, snapshots, positions, and settings belong to the authenticated user.
- **Google and Telegram sign-in:** OpenID Connect with PKCE; link both providers to one account, disconnect a secondary provider, and sign out on all devices.
- **Exchange balances:** Binance Spot, Funding, and selected Futures accounts; Bybit UTA 2.0 and Funding.
- **BSC wallets:** BNB and selected BEP-20 contracts through public RPC, with optional Moralis discovery.
- **Bank accounts:** PrivatBank FOP balances through Autoclient.
- **Manual assets:** cash, crypto, and other assets with optional custom prices and a locked flag.
- **Portfolio overview:** valuation history, source allocation, total and available crypto, cash, and bank balances.
- **Scheduled synchronization:** hourly by default, configurable per user and per source from 5 minutes to 24 hours.
- **Explicit data quality:** stale balances and missing prices are reported; failed synchronization preserves the previous snapshot.

## Quick start

Requirements: Docker Engine with the Compose plugin and Python 3 for configuration generation.

```sh
python3 scripts/setup.py
```

This creates a private `.env` with generated encryption and database secrets. It does not overwrite an existing file. Configure at least one OAuth provider using the [authentication guide](docs/AUTH.md), then start:

```sh
docker compose up --build -d
```

Open [localhost:3000](http://localhost:3000). A first successful sign-in creates an empty portfolio. A synthetic demo is available at [localhost:3000/?demo=1](http://localhost:3000/?demo=1), even without OAuth credentials.

There is no shared password, default user, or privileged owner account. To use both providers for one portfolio, link the second provider from Settings after signing in.

**This release targets fresh installations.** Earlier development databases are not supported and are never converted or deleted automatically. Native development defaults to `contour.db`; Docker uses a new `contour_data` volume. If an existing `.env` points to an older database, configure a new empty database. Incompatible schemas are rejected.

## Documentation

| Guide | Contents |
| --- | --- |
| [Authentication](docs/AUTH.md) | Google and Telegram setup, registration, linking, sessions |
| [Configuration](docs/CONFIGURATION.md) | Environment variables and native development |
| [Integrations](docs/INTEGRATIONS.md) | Supported accounts, valuation, and limitations |
| [Architecture](docs/ARCHITECTURE.md) | Ownership, synchronization, and security boundaries |
| [VM deployment](docs/DEPLOYMENT_VM.md) | HTTPS installation, backups, updates, and troubleshooting |

## Stack

| Component | Technology |
| --- | --- |
| Web application | Next.js 16, React, TypeScript |
| API | FastAPI, Pydantic, SQLAlchemy |
| Database | PostgreSQL 16; SQLite for native development |
| Synchronization | Python worker with database-backed leases |
| Authentication | Google / Telegram OIDC, PyJWT, server-side sessions |
| VM ingress | Caddy with automatic HTTPS |

## Native development

Use Python 3.12+ and Node.js 22+.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
python3 scripts/setup.py
```

Configure OAuth credentials in `.env`. Start each process in a separate terminal from the repository root:

```sh
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

```sh
.venv/bin/python -m backend.worker
```

```sh
npm run dev --prefix frontend
```

Start the API before the worker so the schema exists. Open the exact origin configured by `APP_ORIGIN`, normally `http://localhost:3000`.

## Verification

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
npm run build --prefix frontend
docker compose --env-file .env.example config --no-interpolate --quiet
docker compose --env-file .env.example -f compose.vm.yaml config --no-interpolate --quiet
```

Tests use temporary databases and mocked provider responses. They cover tenant isolation, OAuth signatures and replay protection, identity management, session revocation, valuation precision, worker leases, and failure recovery. Live OAuth credentials and financial accounts must be validated in the target environment.

## Repository layout

```text
backend/              API, authentication, data model, adapters, and worker
frontend/             Dashboard and account interface
tests/                Backend and security regression tests
scripts/setup.py      Secret generation for a fresh installation
deploy/Caddyfile      HTTPS routing for VM deployments
compose.yaml          Local Docker stack
compose.vm.yaml       Standalone VM stack
docs/                 Configuration and operating guides
```

## Scope

Contour is a read-only accounting dashboard. It does not trade, transfer funds, import full transaction ledgers, or monitor liquidation risk in real time. Locked flags are user-supplied metadata, not an exchange guarantee that assets are withdrawable. Unknown prices are excluded from valuation; table filters do not affect totals.

The worker processes sources sequentially. Larger deployments require capacity planning and provider-quota management. Binance Portfolio Margin, Bybit Classic / UTA 1.0, NFT, and DeFi positions are outside the current scope.

The application operator remains a trusted party: stored credentials are encrypted, but the running backend has the key needed to use them. Keep the database, `.env`, and encryption-key backups private.
