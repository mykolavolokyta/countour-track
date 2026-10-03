# Authentication

Contour uses Google and Telegram OpenID Connect. There is no password login or shared owner account. First sign-in creates a user and an empty portfolio when registration is enabled.

## Configuration

Set these variables in `.env`, then restart the backend:

```dotenv
APP_ORIGIN=http://localhost:3000
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
TELEGRAM_CLIENT_ID=
TELEGRAM_CLIENT_SECRET=
ALLOW_REGISTRATION=true
```

The generated `ENCRYPTION_KEY` is also required. A provider button becomes available only when both client credentials are configured. Secrets remain server-side; do not use `NEXT_PUBLIC_*` variables.

The browser origin and callback registrations must match exactly. `localhost` and `127.0.0.1` are different origins. Callback URLs are derived from `APP_ORIGIN`, never from client-supplied headers or redirect parameters.

## Google

In Google Cloud / Google Auth Platform:

1. Create a project and configure Branding and Audience.
2. Create an OAuth client of type **Web application**.
3. Register `http://localhost:3000/api/auth/google/callback` for development.
4. Register `https://your-domain.example/api/auth/google/callback` for deployment.
5. Copy the client ID and secret into `.env`.
6. If the application is in Testing mode, add the intended accounts to Test users.

Contour requests `openid profile` and identifies the user by the signed `sub` claim. No Gmail, Drive, Calendar, or offline access is requested. See [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect).

## Telegram

In **BotFather → your bot → Login Widget**:

1. Register the application origin and exact callback under Allowed URLs.
2. The development callback is `http://localhost:3000/api/auth/telegram/callback`.
3. The deployment callback is `https://your-domain.example/api/auth/telegram/callback`.
4. Copy the **Client ID and Client Secret**. These are OIDC credentials, not the Bot API token.
5. Keep the signing algorithm set to **RS256**, which this application accepts.

Telegram only redirects to registered URLs. If BotFather rejects an HTTP localhost URL, use an HTTPS development origin routing to the entire application, update `APP_ORIGIN`, and register that callback. No tunnel is started automatically.

Only `openid profile` is requested: no phone number or messaging permission. Identity uses the signed `sub`, not a mutable Telegram username. See [Telegram Login and OIDC](https://core.telegram.org/bots/telegram-login).

## Registration and account management

- `ALLOW_REGISTRATION=true`: unseen provider identities create new users.
- `ALLOW_REGISTRATION=false`: existing users can sign in and link another provider; new users cannot register.
- Sign in with one provider, then link the other from **Settings → Sign-in methods** to share a portfolio.
- Separate sign-ins using unlinked identities create independent portfolios. No automatic account merging occurs.
- Identities attached to another user cannot be claimed.
- A secondary provider can be disconnected; the last linked provider cannot be removed. Disconnecting also revokes other browser sessions for that user.
- **Sign out on all devices** revokes only the current user's sessions.

Keep registration enabled during initial setup: there is no bootstrap account that bypasses this policy. There is no separate password recovery or automatic account merge; provider account recovery is handled by Google or Telegram.

## Security model

Application sessions expire after 12 hours. The browser stores a random opaque token in an HttpOnly, SameSite=Lax cookie; the database stores its SHA-256 digest and a CSRF token. HTTPS origins enable Secure cookies. Logout revokes sessions server-side.

OIDC uses PKCE S256, a ten-minute browser-bound single-use state, nonce validation, and signature/issuer/audience/expiry checks. Linking requires the same live session that initiated it. Mutations require exact Origin and CSRF checks. Sessions, OAuth state, and rate limits are stored in the shared database.

Provider tokens are not persisted. JWKS and token endpoints are fixed. Uvicorn strips OAuth query strings from access logs; do not enable unredacted callback query logging in a reverse proxy.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Disabled provider | Client ID, secret, encryption key, and backend restart |
| Redirect mismatch | Scheme, host, port, and exact callback path |
| Generic login failure | System clock, Google test users, Telegram RS256, registered URLs |
| Link rejected | Identity already belongs to another user, or browser session changed |
| Rate limits shared by users | Trusted proxy and client-IP forwarding; the VM stack configures these |
| Unexpected empty portfolio | The second provider was not linked to the first account |
