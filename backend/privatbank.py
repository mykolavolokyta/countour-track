"""Privat24 for business Autoclient: read-only balance snapshots, one IBAN/source."""
from datetime import datetime
from decimal import Decimal
import re

import httpx

from .adapters import SyncError, dec, holding

BASE_URL = 'https://acp.privatbank.ua/api'


def normalize_iban(value):
    value = re.sub(r'\s+', '', value).upper()
    if not re.fullmatch(r'UA\d{27}', value):
        raise ValueError('Invalid Ukrainian IBAN')
    rearranged = value[4:] + '3010' + value[2:4]
    if int(rearranged) % 97 != 1:
        raise ValueError('Invalid IBAN checksum')
    return value


def fiat_price(currency):
    if currency == 'USD':
        return Decimal(1)
    try:
        # Separate public request: never forward a bank token to the FX provider.
        with httpx.Client(timeout=20) as http:
            response = http.get('https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json')
            response.raise_for_status()
            rates = {row['cc']: dec(row['rate']) for row in response.json(parse_float=Decimal)}
        if rates['USD'] <= 0:
            return None
        rate = Decimal(1) / rates['USD'] if currency == 'UAH' else rates[currency] / rates['USD']
        return rate if rate > 0 else None
    except (httpx.HTTPError, KeyError, ValueError, TypeError, ArithmeticError):
        return None


def privatbank_data(iban, credentials):
    try:
        iban = normalize_iban(iban)
    except (ValueError, TypeError):
        raise SyncError('ПриватБанк: перевірте IBAN рахунку ФОП.') from None
    token = credentials.get('bank_token')
    if not token:
        raise SyncError('ПриватБанк: додайте повний токен API «Автоклієнт».')
    headers = {'User-Agent': 'Contour/0.1', 'token': token,
               'Content-Type': 'application/json;charset=utf8', 'Accept': 'application/json'}
    with httpx.Client(timeout=20, follow_redirects=False) as http:
        def get(path, params=None):
            response = http.get(BASE_URL + path, params=params, headers=headers)
            if response.status_code in (401, 403):
                raise SyncError('ПриватБанк: доступ відхилено. Перевірте токен, дозволені IP та доступ до балансів.')
            if response.status_code == 429:
                raise SyncError('ПриватБанк: ліміт запитів. Оновлення буде повторено пізніше.')
            response.raise_for_status()
            try:
                data = response.json(parse_float=Decimal)
            except ValueError:
                raise SyncError('ПриватБанк повернув некоректну відповідь. Попередній баланс збережено.') from None
            if not isinstance(data, dict) or data.get('status') != 'SUCCESS':
                # Never include bank error text: it may echo tokens or account details.
                raise SyncError('ПриватБанк відхилив запит. Перевірте доступ до рахунку та токен API.')
            return data

        settings = get('/statements/settings').get('settings', {})
        if settings.get('phase') != 'WRK' or settings.get('work_balance') != 'N':
            raise SyncError('ПриватБанк виконує регламентні роботи. Попередній баланс збережено.')
        try:
            operating_day = datetime.strptime(settings['today'], '%d.%m.%Y %H:%M:%S')
        except (KeyError, TypeError, ValueError):
            raise SyncError('ПриватБанк не повернув дату операційного дня.') from None
        date = operating_day.strftime('%d-%m-%Y')
        params = {'acc': iban, 'startDate': date, 'endDate': date, 'limit': 100}
        seen, matches = set(), {}
        for _ in range(20):
            data = get('/statements/balance', params)
            if data.get('type') != 'balances' or not isinstance(data.get('balances'), list):
                raise SyncError('ПриватБанк: неочікуваний формат залишків. Попередній баланс збережено.')
            for record in data['balances']:
                try:
                    if normalize_iban(record['acc']) != iban:
                        raise ValueError('Unexpected account')
                    currency = record['currency']
                    if not re.fullmatch(r'[A-Z]{3}', currency):
                        raise ValueError('Invalid currency')
                    amount = dec(record['balanceOut'])
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    raise SyncError('ПриватБанк: не вдалося перевірити залишок вибраного рахунку.') from None
                if currency in matches and matches[currency] != amount:
                    raise SyncError('ПриватБанк повернув суперечливі залишки. Попередні дані збережено.')
                matches[currency] = amount
            more = data.get('exist_next_page')
            if more is False:
                break
            if more is not True:
                raise SyncError('ПриватБанк: не вдалося перевірити повноту відповіді.')
            cursor = data.get('next_page_id')
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise SyncError('ПриватБанк: не вдалося отримати всі сторінки залишків.')
            seen.add(cursor)
            params = dict(params, followId=cursor)
        else:
            raise SyncError('ПриватБанк: перевищено кількість сторінок залишків.')
    if len(matches) != 1:
        raise SyncError('ПриватБанк не повернув однозначний баланс цього IBAN. Перевірте рахунок і права доступу.')
    currency, amount = next(iter(matches.items()))
    rate = fiat_price(currency)
    row = holding(currency, amount, 'ФОП · ' + iban[:4] + '…' + iban[-4:],
                  'bank:privat:' + iban + ':' + currency, usd=amount * rate if rate is not None else None)
    row['asset_category'] = 'bank'
    row['bank_operating_date'] = operating_day.strftime('%Y-%m-%d')
    warnings = [f'Залишок за операційний день банку {operating_day:%d.%m.%Y}. Це balanceOut, не доступний кредитний ліміт.']
    if rate is None:
        warnings.append('Курс НБУ недоступний: залишок збережений у валюті рахунку без оцінки у USD.')
    return [row], [], warnings
