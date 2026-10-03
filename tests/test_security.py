from backend import config


def test_private_routes_require_login(client):
    for path in ('/api/portfolio','/api/history','/api/session'):
        assert client.get(path).status_code==401


def test_csrf_and_origin_required(member):
    member.headers.pop('x-csrf-token')
    assert member.put('/api/settings',json={'interval_minutes':60}).status_code==403
    member.headers['x-csrf-token']=member.get('/api/session').json()['csrf']
    member.headers['origin']='https://other.example'
    assert member.put('/api/settings',json={'interval_minutes':60}).status_code==403


def test_password_login_does_not_exist(client):
    assert client.post('/api/login',json={'password':'anything'}).status_code == 404
    assert 'password' not in client.get('/api/auth/providers').json()


def test_logout_invalidates_access(member):
    assert member.get('/api/portfolio').headers['cache-control']=='no-store'
    assert member.post('/api/logout').status_code==200
    assert member.get('/api/portfolio').status_code==401


def test_validation_does_not_echo_secrets(member):
    response=member.post('/api/sources',json={'name':'','kind':'binance','api_key':'top-secret','api_secret':'do-not-echo','unknown':'private'})
    assert response.status_code==422
    assert 'top-secret' not in response.text and 'do-not-echo' not in response.text


def test_oauth_authorization_codes_are_redacted_from_access_logs():
    import logging
    from backend.main import RedactAuthQuery
    record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0,
                               '%s - "%s %s HTTP/%s" %s',
                               ('127.0.0.1', 'GET', '/api/auth/google/callback?code=secret&state=hidden', '1.1', 303), None)
    assert RedactAuthQuery().filter(record)
    assert 'secret' not in record.getMessage() and 'hidden' not in record.getMessage()
    assert '/api/auth/google/callback' in record.getMessage()
