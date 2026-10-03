from pathlib import Path
import stat
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, select

from backend import db
from backend.db import Session, User, Identity


def test_fresh_database_has_no_bootstrap_user():
    with Session() as session:
        assert session.scalars(select(User)).all() == []
        assert session.scalars(select(Identity)).all() == []
    db.init_db()
    with Session() as session:
        assert session.scalars(select(User)).all() == []


def test_incompatible_database_is_rejected_without_changes(tmp_path, monkeypatch):
    old_engine = create_engine('sqlite:///'+str(tmp_path/'old.db'))
    with old_engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE sources (id VARCHAR PRIMARY KEY, name VARCHAR)')
        conn.exec_driver_sql("INSERT INTO sources VALUES ('existing', 'Untouched')")
    monkeypatch.setattr(db, 'engine', old_engine)
    with pytest.raises(RuntimeError, match='new empty DATABASE_URL'):
        db.init_db()
    assert inspect(old_engine).get_table_names() == ['sources']
    with old_engine.connect() as conn:
        assert conn.exec_driver_sql('SELECT name FROM sources').scalar() == 'Untouched'


def test_setup_generates_private_configuration_without_password_prompt(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path/'test.env'
    command = [sys.executable, str(root/'scripts/setup.py'), '--output', str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    content = output.read_text()
    assert 'OWNER_PASSWORD' not in content
    assert 'ENCRYPTION_KEY=\n' not in content and 'POSTGRES_PASSWORD=\n' not in content
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    secret = next(line.split('=',1)[1] for line in content.splitlines() if line.startswith('ENCRYPTION_KEY='))
    assert secret not in result.stdout
    repeated = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert repeated.returncode != 0 and output.read_text() == content
