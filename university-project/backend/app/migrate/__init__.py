"""Versioned database migrations.

Every change to a table gets a numbered script in app/migrate/versions. When the service starts it brings its database up
to date with `upgrade(engine)`:

* an empty database is built from the current models and marked as being at the newest revision;
* a database made before migrations existed (it has tables but no version mark) is first given the columns later
  builds added, then marked;
* any other database runs the scripts between its revision and the newest.

A test checks that running every script on an empty database gives exactly the schema the models describe, so a model
cannot be changed without a script.
"""
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

log = logging.getLogger("payshield.migrate")
HERE = Path(__file__).parent


def _config(connection, scripts: Path = HERE) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(scripts))
    cfg.attributes["connection"] = connection
    return cfg


BASELINE = "0001"


def _baseline_tables(scripts: Path) -> list[str]:
    """The tables the baseline script creates, found by running it on a scratch database."""
    import tempfile

    from sqlalchemy import create_engine

    with tempfile.TemporaryDirectory() as folder:
        scratch = create_engine(f"sqlite:///{folder}/baseline.db")
        with scratch.begin() as connection:
            command.upgrade(_config(connection, scripts), BASELINE)
        names = sorted(set(inspect(scratch).get_table_names()) - {"alembic_version"})
        scratch.dispose()
    return names


def head_revision(scripts: Path = HERE) -> str:
    cfg = Config()
    cfg.set_main_option("script_location", str(scripts))
    return ScriptDirectory.from_config(cfg).get_current_head()


def current_revision(engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def upgrade(engine, legacy_columns=None, scripts: Path = HERE) -> str:
    """Bring the database to the newest revision. Returns what was done: created, stamped, upgraded or current."""
    from app.db import Base

    tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        cfg = _config(connection, scripts)
        if not tables:
            Base.metadata.create_all(connection)
            command.stamp(cfg, "head")
            log.info("Database created at revision %s", head_revision(scripts))
            return "created"
        if "alembic_version" not in tables:
            # Made by a build from before migrations. Give it the columns the baseline has, mark it as the baseline,
            # then run every later script like any other database.
            if legacy_columns is not None:
                legacy_columns(connection)
            wanted = [Base.metadata.tables[name] for name in _baseline_tables(scripts) if name not in tables]
            Base.metadata.create_all(connection, tables=wanted)  # a whole table the baseline has and the old database lacks
            command.stamp(cfg, BASELINE)
            command.upgrade(cfg, "head")
            log.info("Database from before migrations marked at %s and upgraded to %s", BASELINE, head_revision(scripts))
            return "stamped"
    before = current_revision(engine)
    if before == head_revision(scripts):
        return "current"
    with engine.begin() as connection:
        command.upgrade(_config(connection, scripts), "head")
    log.info("Database upgraded from %s to %s", before, head_revision(scripts))
    return "upgraded"


def new_revision(message: str, scripts: Path = HERE) -> Path | None:
    """Write the next migration script from the difference between the scripts and the models.
    Returns the new file, or None when the models already match the scripts (nothing to write)."""
    import re
    import tempfile

    from sqlalchemy import create_engine

    slug = re.sub(r"[^a-z0-9]+", "_", message.lower()).strip("_")[:40] or "change"
    with tempfile.TemporaryDirectory() as folder:
        engine = create_engine(f"sqlite:///{folder}/scratch.db")  # a scratch database built from the scripts alone
        with engine.begin() as connection:
            command.upgrade(_config(connection, scripts), "head")
        written: list[Path] = []

        def skip_if_empty(_context, _revision, directives):
            if directives[0].upgrade_ops.is_empty():
                directives[:] = []

        head = head_revision(scripts)
        next_id = f"{int(head) + 1:04d}" if head.isdigit() else None
        with engine.begin() as connection:
            result = command.revision(_config(connection, scripts), message=slug, autogenerate=True, rev_id=next_id, process_revision_directives=skip_if_empty)
        if result:
            written.append(Path(result.path))
        return written[0] if written else None
