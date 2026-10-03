# Integrations and valuation

Add sources from the dashboard after signing in. Sources and encrypted credentials belong to the current user. Each source can override that user's synchronization interval.

## Binance

Create a read-only API key with trading, transfers, and withdrawals disabled. The adapter checks key permissions and rejects write-enabled keys.

- Spot uses free plus locked balances.
- Funding includes free, locked, frozen, and withdrawing amounts.
- Select USDⓈ-M and/or COIN-M Futures accounts when adding the source.
- Futures valuation uses margin balance, including unrealized PnL, once per asset.

Position notional and leverage do not increase portfolio value. Portfolio Margin, Earn, and subaccounts are outside the supported scope. Add each exchange account only once: different keys for the same account will duplicate its balances.

References: [Funding balances](https://developers.binance.com/docs/wallet/asset/funding-wallet), [Futures account](https://developers.binance.com/docs/derivatives/usds-margined-futures/account/rest-api/Account-Information-V3).

## Bybit

Use a Read-only API key. The adapter supports UTA 2.0, its shared Spot/Futures equity, and a separate Funding balance. It reads open linear USDT/USDC and inverse positions with pagination.

Per-coin equity and USD value are used; Spot inside UTA is not added separately. Classic, UTA 1.0, regional API variants, and subaccounts are not supported.

References: [UTA wallet balance](https://bybit-exchange.github.io/docs/v5/account/wallet-balance), [Funding](https://bybit-exchange.github.io/docs/v5/asset/balance/all-balance), [Positions](https://bybit-exchange.github.io/docs/v5/position).

## BNB Smart Chain

### Public RPC

Select the public RPC option and enter a public BSC address. BNB is included automatically. Add up to 20 BEP-20 contract addresses, one per line, to track tokens. An empty contract list means BNB only.

RPC does not discover arbitrary tokens. The adapter verifies chain ID 56, reads balances at one block, and uses fallback endpoints. A failed required call rejects the entire snapshot instead of silently substituting zero. A failed symbol lookup falls back to a shortened contract address.

Prices come from CoinGecko by BSC contract address, not by token symbol. Configure `COINGECKO_DEMO_API_KEY` if needed. A missing price preserves the quantity but leaves the USD value unknown.

### Moralis

Set `MORALIS_API_KEY` on the server and select Moralis in the source editor for automatic token discovery. Availability and quotas depend on the configured provider account. Switching to RPC changes coverage: explicitly add all desired token contracts.

NFT and DeFi positions are not included. Wallet private keys and seed phrases are never required.

References: [BSC RPC endpoints](https://docs.bnbchain.org/bnb-smart-chain/developers/json_rpc/json-rpc-endpoint/), [CoinGecko contract pricing](https://docs.coingecko.com/demo/reference/simple-token-price), [Moralis balances](https://docs.moralis.com/data-api/evm/wallet/token-balances).

## PrivatBank business accounts

In Privat24 for business, create an Autoclient API integration. Enable only **“Отримання балансів і транзакцій за рахунками”** (account balances and transactions). Copy the full API token into the application and provide the Ukrainian IBAN.

One IBAN is one source; duplicate active IBANs within a user's portfolio are rejected. An empty token field during editing retains the existing encrypted token. Token permissions must be restricted at the bank; Contour uses only read endpoints and does not inspect those permissions automatically.

The adapter reads `balanceOut` for the bank's current operating day and verifies all pages. Opening balances, turnover, local-currency equivalents, and credit limits are not added separately. The operating date is displayed in source notes and may differ from the calendar date.

Zero and negative balances are supported. Missing, conflicting, incomplete, or maintenance responses preserve the previous snapshot. USD is valued directly; other supported currencies use the official NBU exchange rates. A missing rate leaves the balance unpriced.

This is an accounting balance, not a guarantee of the amount immediately available for transfer. FOP groups, personal cards, transaction import, and payments are not implemented.

Reference: [PrivatBank accounting integrations](https://privatbank.ua/business/intehratsiya).

## Manual assets

Select Cash, Crypto, or Other, then provide a symbol and quantity. A manual unit price in USD overrides automatic pricing. Unknown symbols without a manual price remain unpriced.

For assets on an exchange without API access, use the exchange name as the source name and select Crypto. Mark locked assets explicitly. If only part of a holding is locked, split it into separate manual sources. Do not manually duplicate balances already included by an API source.

Locked assets count once toward total portfolio and crypto value. The available-crypto figure excludes holdings marked locked. The flag is accounting metadata and does not place restrictions on the actual asset.

## Shared valuation rules

- Crypto symbols supported by the exchange/manual price adapter map to explicit CoinGecko IDs. Unknown assets are not guessed.
- Stablecoins use provider prices rather than an unconditional 1:1 assumption.
- UAH and EUR manual balances use NBU reference rates to USD; these are not cash-exchange quotes.
- Unpriced assets are excluded from monetary totals and hidden from the table, but remain visible as missing-price counts.
- The under-$1 filter uses absolute holding value, including negative balances. It does not change totals or history.
- Balances synchronize independently; cross-source transfers may briefly produce inconsistent totals.

Live integrations should be checked with the intended read-only accounts. Automated tests use controlled responses, not real funds.
