export type Chain = 'bsc' | 'ethereum' | 'arbitrum' | 'optimism' | 'base';
export type Kind = 'binance' | 'bybit' | 'bsc' | 'manual' | 'privatbank';
export type Source = {
  id: string; name: string; kind: Kind; config: {chain?: Chain | 'all'; contracts_by_chain?: Partial<Record<Chain, string[]>>; iban?: string; asset_category?: 'cash' | 'crypto' | 'other'; locked?: boolean; provider?: 'rpc' | 'moralis'; token_contracts?: string[]; address?: string; symbol?: string; quantity?: string; price_usd?: string | null; futures?: string[]};
  interval_minutes: number | null; effective_interval: number; status: string; error: string | null;
  last_success: number | null; last_attempt: number | null; next_sync: number; has_credentials: boolean;
  total_usd: string | null; warnings: string[]; unpriced: number; stale: boolean;
};
export type Holding = {locked?: boolean; asset_category?: 'cash' | 'crypto' | 'other' | 'bank'; bank_operating_date?: string; asset_id: string; symbol: string; quantity: string; account: string; value_usd: string | null; pnl: string | null; source_id: string; source_name: string; kind: Kind; observed_at: number; stale: boolean};
export type Position = {symbol: string; account: string; side: string; size: string; entry: string; mark: string; pnl: string; settle: string; leverage: string | null; liquidation: string | null; source_id: string; source_name: string; observed_at: number};
export type Portfolio = {locked_usd?: string; locked_count?: number; locked_unpriced?: number; sources: Source[]; holdings: Holding[]; positions: Position[]; total_usd: string; unpriced: number; incomplete: boolean; interval_minutes: number; as_of: number};
export type Point = {at: number; value_usd: string; unpriced: number};
