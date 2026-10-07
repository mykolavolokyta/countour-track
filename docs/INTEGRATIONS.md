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

## EVM wallets across networks

New wallet forms default to **All supported networks**. Enter one public
address to aggregate Ethereum, BSC, Arbitrum One, Optimism and Base under one
source. Every sync checks all five networks, including those whose last
balance was zero. This covers the supported registry, not every EVM blockchain
in existence.

The native coin is automatic (BNB on BSC and ETH on the other four networks).
The optional contract section accepts up to 20 ERC-20 contracts **per network**.
An empty list means native coins only: selecting all networks does not enable
automatic token discovery. RPC mode does not enumerate arbitrary tokens,
NFTs, DeFi positions or staked balances.

Existing single-network sources keep their coverage until edited. Select
**All supported networks** to expand one; its previous contract list stays
assigned to its original network. If other active sources track the same
address in overlapping networks, disconnect those duplicates first. The API
rejects overlapping coverage within a user's portfolio to prevent double
counting. Separate users can independently track the same public wallet.
Moralis discovery remains available for BSC-only sources.

Each network verifies its chain ID and reads balances at one block height.
Blocks are independent across networks. The combined snapshot is saved only
when all networks succeed. If any network fails, the previous complete wallet
snapshot remains visible and the source reports an error naming the failed
network; initial failure leaves the wallet without a snapshot. Zero balances
are valid successful results. Token identity and price caches are scoped by
network, and missing prices stay unknown instead of borrowing a symbol price.

Public RPC needs no paid subscription or wallet signature. Public endpoints
and CoinGecko have quotas and availability limits. Hourly updates for a small
number of wallets are the intended starting point, not unlimited free service.
An optional CoinGecko Demo key can be configured on the server.

### API compatibility

The API retains `kind: "bsc"` for existing clients. `config.chain: "all"` enables
combined coverage; `contracts_by_chain` maps network keys to contract lists.
For example:

```json
{
  "kind": "bsc",
  "name": "My EVM wallet",
  "address": "0x1111111111111111111111111111111111111111",
  "provider": "rpc",
  "chain": "all",
  "contracts_by_chain": {}
}
```

Single-network keys are `bsc`, `ethereum`, `arbitrum`, `optimism`, `base` and
use `token_contracts`. Omitted chain defaults to BSC on create for compatibility;
on edit it preserves the existing source's network. Expanding a single-network
source to `all` is supported; replacing or narrowing its network is rejected.
For an expansion with omitted `contracts_by_chain`, the old contract list is
retained automatically. An explicit empty mapping clears the watchlists.
Non-BSC asset IDs use `evm:<chain-id>:<contract-or-native>`; BSC IDs are unchanged.

### Automatic token discovery and free plans

Discovery without contract lists requires an indexer. Alchemy's documented
Free tier includes Token API access and 30 million compute units per month
(as checked October 7, 2026). Network coverage, per-method costs and quotas
must be checked for the chosen endpoint. This is a possible future integration;
Contour's all-network RPC mode does **not** currently call Alchemy or discover
tokens automatically. No Alchemy API key is needed for the implemented mode.

References: [Ethereum JSON-RPC](https://ethereum.org/developers/docs/apis/json-rpc/),
[PublicNode endpoints](https://www.publicnode.com/),
[Alchemy pricing plans](https://www.alchemy.com/docs/reference/pricing-plans),
[Alchemy free-tier limits](https://support.alchemy.com/articles/5827006872-what-is-included-in-the-free-tier).
