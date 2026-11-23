"""Database migrations: a new database, an old one, and the guarantee that the scripts and the models agree."""
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app import migrate
from app.db import Base
from app.runtime import _add_legacy_columns


def fresh_engine(tmp_path, name="db"):
    """A blank database: the real Postgres when TEST_DATABASE_URL is set (emptied first), otherwise a SQLite file."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        from helpers import external_services

        external_services()
        return create_engine(url)
    return create_engine(f"sqlite:///{tmp_path}/{name}.db")


def add_user(engine, **over):
    users = Base.metadata.tables["users"]
    values = dict(id="u1", email="a@b.lk", full_name="A B", password_hash="x", role="auditor", status="active", created_at=datetime(2026, 1, 1), totp_enrolled=False)
    values.update(over)
    with engine.begin() as connection:
        connection.execute(users.insert().values(**{k: v for k, v in values.items() if k in users.c}))


def table_names(engine):
    return set(inspect(engine).get_table_names())


# ------------------------------------------------------------------ the scripts and the models agree

def test_running_every_script_on_an_empty_database_gives_exactly_the_schema_the_models_describe(tmp_path):
    engine = fresh_engine(tmp_path)
    with engine.begin() as connection:
        command.upgrade(migrate._config(connection), "head")  # the scripts alone, no create_all
    assert table_names(engine) - {"alembic_version"} == set(Base.metadata.tables)
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata)
    assert differences == [], "a model changed without a migration script: " + str(differences)[:600]


def test_the_scripts_form_one_straight_line_with_a_single_head():
    from alembic.script import ScriptDirectory

    cfg = migrate._config(None)
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    revisions = list(script.walk_revisions())
    assert all(len(r.nextrev) <= 1 for r in revisions), "two scripts branch from the same parent"
    assert revisions[-1].down_revision is None


# ------------------------------------------------------------------ the three kinds of starting point

def test_an_empty_database_is_created_and_marked_current(tmp_path):
    engine = fresh_engine(tmp_path)
    assert migrate.upgrade(engine) == "created"
    assert set(Base.metadata.tables) <= table_names(engine)
    assert migrate.current_revision(engine) == migrate.head_revision()
    assert migrate.upgrade(engine) == "current"


def test_a_database_at_an_older_revision_is_upgraded_and_keeps_its_data(tmp_path):
    scripts = tmp_path / "scripts"
    shutil.copytree(migrate.HERE, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "versions" / "0004_add_nickname.py").write_text(
        '"""add a nickname"""\nimport sqlalchemy as sa\nfrom alembic import op\n\nrevision = "0004"\ndown_revision = "0003"\nbranch_labels = None\ndepends_on = None\n\n\n'
        'def upgrade() -> None:\n    op.add_column("users", sa.Column("nickname", sa.String(40), nullable=True))\n\n\n'
        'def downgrade() -> None:\n    op.drop_column("users", "nickname")\n'
    )
    engine = fresh_engine(tmp_path)
    old = Path(migrate.HERE)
    assert migrate.upgrade(engine, scripts=old) == "created"  # built at the real head
    add_user(engine)
    assert migrate.upgrade(engine, scripts=scripts) == "upgraded"
    assert "nickname" in {c["name"] for c in inspect(engine).get_columns("users")}
    assert migrate.current_revision(engine) == "0004"
    with engine.connect() as connection:
        email, nickname = connection.execute(text("SELECT email, nickname FROM users WHERE id='u1'")).one()
    from app.config import Settings
    from app.crypto import open_field

    assert open_field(email, Settings.from_env().encryption_key, "email") == "a@b.lk" and nickname is None
    assert migrate.upgrade(engine, scripts=scripts) == "current"


@pytest.mark.skipif(bool(os.environ.get("TEST_DATABASE_URL")), reason="the first pilot only ever ran on SQLite")
def test_a_database_from_before_migrations_gets_its_newer_columns_and_keeps_its_data(tmp_path):
    path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:  # the first migration only: the tables as the first pilot had them
        command.upgrade(migrate._config(connection), migrate.BASELINE)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    # Take away what later builds added, to look like the first pilot's database.
    newer = {"users": ["username", "totp_failures", "totp_locked_until", "nic", "balance_lkr", "registered", "registered_at", "must_change_password", "password_changed_at", "face_ref", "face_hash"],
             "cases": ["liveness_strikes", "knowledge_round", "face_score", "face_verdict"], "case_events": ["fallback_from"], "reports": ["sha256"], "audit_log": ["prev_hash", "entry_hash"]}
    add_user(engine, id="u1", email="old@b.lk", full_name="Old Customer", role="customer")
    raw = sqlite3.connect(path)
    for table, columns in newer.items():
        keep = [row[1] for row in raw.execute(f"PRAGMA table_info({table})") if row[1] not in columns]
        raw.execute(f"CREATE TABLE {table}_old AS SELECT {', '.join(keep)} FROM {table}") # noqa: S608 - table and column names come from the literal dictionary above
        raw.execute(f"DROP TABLE {table}")
        raw.execute(f"ALTER TABLE {table}_old RENAME TO {table}")
    raw.commit()
    raw.close()
    engine = create_engine(f"sqlite:///{path}")
    assert "face_ref" not in {c["name"] for c in inspect(engine).get_columns("users")}
    assert "alembic_version" not in table_names(engine)

    assert migrate.upgrade(engine, legacy_columns=_add_legacy_columns) == "stamped"

    have = {c["name"] for c in inspect(engine).get_columns("users")}
    assert {"username", "nic", "balance_lkr", "face_ref", "must_change_password"} <= have
    assert migrate.current_revision(engine) == migrate.head_revision()
    with engine.connect() as connection:
        email, *rest = connection.execute(text("SELECT email, full_name, balance_lkr, registered FROM users WHERE id='u1'")).one()
    # The 0003 migration encrypted the personal field; the value is unchanged once opened with the key.
    from app.config import Settings
    from app.crypto import open_field

    assert email.startswith("siv1$") and "old@b.lk" not in email
    assert open_field(email, Settings.from_env().encryption_key, "email") == "old@b.lk" and rest == ["Old Customer", 0, 0]
    assert migrate.upgrade(engine, legacy_columns=_add_legacy_columns) == "current"


def test_the_service_starts_on_each_kind_of_database(boot):
    """The fixture started the real service on an empty database; its version mark is the newest."""
    assert migrate.current_revision(boot.engine) == migrate.head_revision()
    from app.runtime import init_runtime

    init_runtime()  # a second start on the same database changes nothing
    assert migrate.current_revision(boot.engine) == migrate.head_revision()


def test_the_new_migration_command_writes_nothing_while_the_models_match_the_scripts():
    assert migrate.new_revision("no change") is None


def test_the_new_migration_command_writes_a_script_when_a_model_has_changed(tmp_path):
    import sqlalchemy as sa

    scripts = tmp_path / "scripts"
    shutil.copytree(migrate.HERE, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    users = Base.metadata.tables["users"]
    users.append_column(sa.Column("nickname", sa.String(40), nullable=True))  # a pretend model change
    try:
        path = migrate.new_revision("add nickname", scripts)
    finally:
        users._columns.remove(users.c.nickname)
    assert path is not None and path.name.startswith("0004_add_nickname")
    text_ = path.read_text()
    assert "nickname" in text_ and 'down_revision = "0003"' in text_.replace("'", '"')


def test_the_head_command_prints_the_newest_revision(capsys, monkeypatch):
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["app.migrate", "head"])
    runpy.run_module("app.migrate", run_name="__main__")
    assert capsys.readouterr().out.strip() == migrate.head_revision()
