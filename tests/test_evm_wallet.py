from decimal import Decimal

import pytest

from backend import config, evm_wallet, services
from backend.adapters import SyncError, holding
from backend.db import Session, Source, now

ADDRESS = '0x' + 'a' * 40
TOKEN = '0x' + 'b' * 40
BODY = {'kind': 'bsc', 'name': 'All networks', 'address': ADDRESS, 'chain': 'all', 'provider': 'rpc'}


def test_all_networks_aggregated_and_contracts_scoped(monkeypatch):
    calls = []
    def read(address, contracts, chain):
        calls.append((address, contracts, chain))
        network = config.EVM_CHAINS[chain]
        return [holding(network['symbol'], Decimal('1'), network['name'],
                        f"evm:{network['id']}:native", usd=Decimal('10'))], [], [chain]
    monkeypatch.setattr(evm_wallet, 'bsc_rpc_data', read)
    rows, positions, warnings = evm_wallet.evm_wallet_data(ADDRESS, {'base': [TOKEN]})
    assert [call[2] for call in calls] == list(config.EVM_CHAINS)
    assert all(address == ADDRESS for address, _, _ in calls)
    assert all(contracts == ([TOKEN] if chain == 'base' else []) for _, contracts, chain in calls)
    assert len(rows) == len(set(row['asset_id'] for row in rows)) == 5
    assert not positions and warnings == list(config.EVM_CHAINS)


def test_failed_network_preserves_entire_combined_balance(member, monkeypatch):
    failing = []
    def read(address, contracts, chain):
        if chain in failing:
            raise SyncError('RPC недоступний.')
        return [holding('ETH', Decimal('1'), chain, f'evm:{chain}:native', usd=Decimal('10'))], [], []
    monkeypatch.setattr(evm_wallet, 'bsc_rpc_data', read)
    source_id = member.post('/api/sources', json=BODY).json()['id']
    assert services.sync_source(source_id)
    assert member.get('/api/portfolio').json()['total_usd'] == '50'
    failing.append('base')
    with Session() as db:
        db.get(Source, source_id).next_sync = now() - 1
        db.commit()
    assert services.sync_source(source_id) is False
    portfolio = member.get('/api/portfolio').json()
    assert portfolio['total_usd'] == '50'
    assert len(portfolio['holdings']) == 5
    assert portfolio['sources'][0]['status'] == 'error'
    assert 'Base' in portfolio['sources'][0]['error']


@pytest.mark.parametrize('first,second', [('all', 'base'), ('base', 'all'), ('all', 'all')])
def test_overlapping_wallets_rejected(member, first, second):
    assert member.post('/api/sources', json=dict(BODY, chain=first)).status_code == 201
    assert member.post('/api/sources', json=dict(BODY, chain=second)).status_code == 409


def test_upgrade_preserves_contracts_and_blocks_overlapping_sources(member):
    source_id = member.post('/api/sources', json=dict(BODY, chain='base', token_contracts=[TOKEN])).json()['id']
    other_id = member.post('/api/sources', json=dict(BODY, chain='ethereum')).json()['id']
    assert member.put('/api/sources/' + source_id, json=BODY).status_code == 409
    assert member.delete('/api/sources/' + other_id).status_code == 200
    updated = member.put('/api/sources/' + source_id, json=BODY)
    assert updated.status_code == 200
    assert updated.json()['config']['contracts_by_chain'] == {'base': [TOKEN]}
    assert updated.json()['config']['chain'] == 'all'
    assert 'token_contracts' not in updated.json()['config']
    # Older clients must not reset coverage when renaming a wallet.
    renamed = member.put('/api/sources/' + source_id, json={'kind': 'bsc', 'name': 'Renamed', 'address': ADDRESS})
    assert renamed.status_code == 200
    assert renamed.json()['config'] == updated.json()['config']


@pytest.mark.parametrize('mapping', [
    {'unknown': [TOKEN]}, {'base': ['bad']}, {'base': [TOKEN, TOKEN]},
    {'base': ['0x' + '0' * 40]}, {'base': [TOKEN] * 21},
])
def test_invalid_network_contract_lists_rejected(member, mapping):
    assert member.post('/api/sources', json=dict(BODY, contracts_by_chain=mapping)).status_code == 422


def test_same_contract_on_two_networks_allowed_and_normalized(member):
    upper = '0x' + 'B' * 40
    mapping = {'ethereum': [upper], 'base': [upper]}
    response = member.post('/api/sources', json=dict(BODY, contracts_by_chain=mapping))
    assert response.status_code == 201
    assert response.json()['config']['contracts_by_chain'] == {'ethereum': [TOKEN], 'base': [TOKEN]}


def test_all_requires_scoped_contracts_and_rpc(member):
    assert member.post('/api/sources', json=dict(BODY, token_contracts=[TOKEN])).status_code == 422
    assert member.post('/api/sources', json=dict(BODY, provider='moralis')).status_code == 422


def test_legacy_edit_cannot_assign_contracts_to_unspecified_network(member):
    source_id = member.post('/api/sources', json=BODY).json()['id']
    response = member.put('/api/sources/' + source_id, json={
        'kind': 'bsc', 'name': 'Old client', 'address': ADDRESS, 'token_contracts': [TOKEN]})
    assert response.status_code == 422
