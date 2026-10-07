"""Aggregate one public address across every configured, supported EVM chain."""
from . import config
from .adapters import SyncError
from .bsc_rpc import bsc_rpc_data


def evm_wallet_data(address, contracts_by_chain):
    if not isinstance(contracts_by_chain, dict) or set(contracts_by_chain) - config.EVM_CHAINS.keys():
        raise SyncError('Перевірте мережі у списку контрактів.')
    rows, warnings = [], []
    # Publish only a complete set. A failed chain must never look like a zero
    # balance or silently disappear from the combined wallet valuation.
    for chain, network in config.EVM_CHAINS.items():
        try:
            balances, _, notes = bsc_rpc_data(address, contracts_by_chain.get(chain, []), chain)
        except SyncError as exc:
            raise SyncError(f"{network['name']}: {exc} Спільний баланс гаманця не оновлено.") from None
        rows.extend(balances)
        warnings.extend(notes)
    return rows, [], warnings
