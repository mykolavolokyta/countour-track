import type {Chain} from './types';

export const networks: Record<Chain, {name: string; symbol: string; explorer: string}> = {
  bsc: {name: 'BNB Smart Chain', symbol: 'BNB', explorer: 'https://bscscan.com'},
  ethereum: {name: 'Ethereum', symbol: 'ETH', explorer: 'https://etherscan.io'},
  arbitrum: {name: 'Arbitrum One', symbol: 'ETH', explorer: 'https://arbiscan.io'},
  optimism: {name: 'Optimism', symbol: 'ETH', explorer: 'https://optimistic.etherscan.io'},
  base: {name: 'Base', symbol: 'ETH', explorer: 'https://basescan.org'},
};
