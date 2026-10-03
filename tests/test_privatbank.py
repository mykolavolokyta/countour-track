from decimal import Decimal

import httpx
import pytest

from backend import privatbank as bank, services
from backend.adapters import SyncError
from backend.db import Session, Source, now
from backend.security import unseal

IBAN = 'UA943052990000026100050001037'
TOKEN = 'private-test-token'
SETTINGS = {'status': 'SUCCESS', 'settings': {
    'phase': 'WRK', 'work_balance': 'N', 'today': '02.10.2026 00:00:00'}}
BODY = {'kind': 'privatbank', 'name': 'ФОП', 'iban': IBAN, 'bank_token': TOKEN}


def page(amount='100.25', currency='USD', **extra):
    return dict(status='SUCCESS', type='balances', exist_next_page=False,
                balances=[{'acc': IBAN, 'currency': currency, 'balanceOut': amount,
                           'balanceIn': '9000', 'balanceOutEq': '8000'}], **extra)


def mock_bank(monkeypatch, responses):
    real_client = httpx.Client
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'GET'
        assert request.url.host == 'acp.privatbank.ua'
        assert request.headers['token'] == TOKEN
        value = responses.pop(0)
        return httpx.Response(value if isinstance(value, int) else 200,
                              json={} if isinstance(value, int) else value)
    monkeypatch.setattr(bank.httpx, 'Client',
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    return calls


def test_operating_day_balance_and_pagination(monkeypatch):
    first = page()
    first.update(exist_next_page=True, next_page_id='cursor')
    calls = mock_bank(monkeypatch, [SETTINGS, first, page()])
    rows, positions, warnings = bank.privatbank_data(IBAN, {'bank_token': TOKEN})
    assert len(rows) == 1
    assert rows[0]['quantity'] == rows[0]['value_usd'] == '100.25'
    assert rows[0]['bank_operating_date'] == '2026-10-02'
    assert rows[0]['asset_category'] == 'bank'
    assert positions == []
    assert calls[1].url.params['acc'] == IBAN
    assert calls[1].url.params['startDate'] == '02-10-2026'
    assert calls[2].url.params['followId'] == 'cursor'
    assert 'utf8' in calls[1].headers['content-type']


@pytest.mark.parametrize('amount', ['0', '-12.50'])
def test_zero_and_negative_bank_balances(monkeypatch, amount):
    mock_bank(monkeypatch, [SETTINGS, page(amount)])
    rows, _, _ = bank.privatbank_data(IBAN, {'bank_token': TOKEN})
    assert rows[0]['value_usd'] == amount


@pytest.mark.parametrize('bad', [
    {'status': 'ERROR', 'message': TOKEN},
    {'status': 'SUCCESS', 'type': 'balances', 'exist_next_page': False, 'balances': []},
    page('NaN'),
    dict(page(), exist_next_page=True),
    dict(page(), balances=[{'acc': 'UA000000000000000000000000000', 'currency': 'USD', 'balanceOut': '100'}]),
])
def test_invalid_bank_response_never_becomes_zero(monkeypatch, bad):
    mock_bank(monkeypatch, [SETTINGS, bad])
    with pytest.raises(SyncError) as error:
        bank.privatbank_data(IBAN, {'bank_token': TOKEN})
    assert TOKEN not in str(error.value)


def test_maintenance_does_not_query_balance(monkeypatch):
    settings = dict(SETTINGS, settings=dict(SETTINGS['settings'], work_balance='Y'))
    calls = mock_bank(monkeypatch, [settings])
    with pytest.raises(SyncError):
        bank.privatbank_data(IBAN, {'bank_token': TOKEN})
    assert len(calls) == 1


def test_conflicting_pages_rejected(monkeypatch):
    first = dict(page(), exist_next_page=True, next_page_id='cursor')
    mock_bank(monkeypatch, [SETTINGS, first, page('999')])
    with pytest.raises(SyncError, match='суперечливі'):
        bank.privatbank_data(IBAN, {'bank_token': TOKEN})


def test_fiat_conversion_and_missing_rate(monkeypatch):
    for rate in (Decimal('0.025'), None):
        with monkeypatch.context() as m:
            mock_bank(m, [SETTINGS, page('400', 'UAH')])
            m.setattr(bank, 'fiat_price', lambda _: rate)
            rows, _, _ = bank.privatbank_data(IBAN, {'bank_token': TOKEN})
            assert rows[0]['quantity'] == '400'
            assert rows[0]['value_usd'] == ('10.000' if rate else None)


def test_nbu_request_has_no_bank_credentials(monkeypatch):
    real_client = httpx.Client
    def handle(request):
        assert request.url.host == 'bank.gov.ua'
        assert 'token' not in request.headers
        return httpx.Response(200, json=[{'cc': 'USD', 'rate': 40}, {'cc': 'EUR', 'rate': 44}])
    monkeypatch.setattr(bank.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    assert bank.fiat_price('UAH') == Decimal('0.025')
    assert bank.fiat_price('EUR') == Decimal('1.1')


def test_source_credentials_validation_and_edit(member):
    response = member.post('/api/sources', json=BODY)
    assert response.status_code == 201
    source_id = response.json()['id']
    assert TOKEN not in response.text
    with Session() as db:
        stored = db.get(Source, source_id).credentials
        assert TOKEN not in stored
        assert unseal(stored) == {'bank_token': TOKEN}
    edit = {k: v for k, v in BODY.items() if k != 'bank_token'}
    assert member.put('/api/sources/' + source_id, json=edit).status_code == 200
    with Session() as db:
        assert db.get(Source, source_id).credentials == stored
    assert member.post('/api/sources', json=BODY).status_code == 409
    assert member.post('/api/sources', json=dict(BODY, iban='UA00' + IBAN[4:])).status_code == 422
    assert member.post('/api/sources', json=edit).status_code == 422
    assert member.put('/api/sources/' + source_id, json=dict(edit, api_key='x', api_secret='y')).status_code == 422


def test_bank_failure_keeps_prior_snapshot(member, monkeypatch):
    source_id = member.post('/api/sources', json=BODY).json()['id']
    mock_bank(monkeypatch, [SETTINGS, page(), 401])
    assert services.sync_source(source_id)
    with Session() as db:
        db.get(Source, source_id).next_sync = now() - 1
        db.commit()
    assert not services.sync_source(source_id)
    response = member.get('/api/portfolio')
    assert response.json()['total_usd'] == '100.25'
    assert response.json()['sources'][0]['status'] == 'error'
    assert TOKEN not in response.text
