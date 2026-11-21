"""Alembic's runtime. The connection is handed in by app.migrate, so migrations run on the same database the app uses."""
from alembic import context

import app.models  # noqa: F401  (registers every table on the metadata)
from app.db import Base

config = context.config
connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Migrations run through app.migrate.upgrade(engine); there is no standalone mode.")

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    compare_type=True,
    render_as_batch=connection.dialect.name == "sqlite",  # SQLite can only change a table by rebuilding it
)
with context.begin_transaction():
    context.run_migrations()
