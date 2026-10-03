import {Holding, Kind, Point, Portfolio, Source} from './types';

export function demoData(): {portfolio: Portfolio; history: Point[]} {
  const now = Math.floor(Date.now() / 1000);
  const source = (id: string, name: string, kind: Kind, total: string): Source => ({id, name, kind, config: {}, interval_minutes: null, effective_interval: 60, status: 'ok', error: null, last_success: now - 420, last_attempt: now - 420, next_sync: now + 3180, has_credentials: false, total_usd: total, warnings: [], unpriced: 0, stale: false});
  const sources = [source('1','Binance','binance','24638.50'), source('2','Bybit','bybit','10840.75'), source('3','Основний гаманець','bsc','6390.00'), source('4','Готівка USD','manual','2500.00')];
  sources[0].config = {futures:['usdm']};
  sources[2].config = {address:'0x0000000000000000000000000000000000000000'};
  sources[3].config = {symbol:'USD', quantity:'2500', price_usd:null};
  const row = (i: number, symbol: string, quantity: string, value: string, account: string, pnl: string | null = null): Holding => ({asset_id: sources[i].kind === 'bsc' ? `bsc:56:demo-${symbol}` : `exchange:${symbol}`, symbol, quantity, value_usd:value, account, pnl, source_id:sources[i].id, source_name:sources[i].name, kind:sources[i].kind, observed_at:now-420, stale:false});
  const holdings = [row(0,'BTC','0.18','17640.00','Spot'),row(0,'ETH','1.25','3375.00','Spot'),row(0,'USDT','2000','2000.00','Funding'),row(0,'USDT','1623.50','1623.50','Futures USDⓈ-M','123.50'),row(1,'USDT','9840.75','9840.75','Unified · Spot + Futures','-159.25'),row(1,'USDC','1000','1000.00','Funding'),row(2,'BNB','6','3540.00','BSC'),row(2,'USDT','2850','2850.00','BSC'),row(3,'USD','2500','2500.00','Ручний облік')];
  const history = Array.from({length:31},(_,i)=>({at:now-(30-i)*86400,value_usd:(40100+i*132+Math.sin(i*.75)*630+(i>20?300:0)).toFixed(2),unpriced:0}));
  history[30].value_usd = '44369.25';
  return {portfolio:{sources,holdings,positions:[{symbol:'BTCUSDT',account:'Futures USDⓈ-M',side:'LONG',size:'0.05',entry:'95530',mark:'98000',pnl:'123.50',settle:'USDT',leverage:'3',liquidation:'64810',source_id:'1',source_name:'Binance',observed_at:now-420},{symbol:'ETHUSDT',account:'Unified',side:'LONG',size:'1.5',entry:'2806.1667',mark:'2700',pnl:'-159.25',settle:'USDT',leverage:'2',liquidation:'1410',source_id:'2',source_name:'Bybit',observed_at:now-420}],total_usd:'44369.25',unpriced:0,incomplete:false,interval_minutes:60,as_of:now}, history};
}
