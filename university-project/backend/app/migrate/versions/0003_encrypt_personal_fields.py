"""encrypt_personal_fields

Widens users.email, phone, nic and account_number so they can hold ciphertext, then encrypts every existing value with
the configured ENCRYPTION_KEY (siv1$, deterministic, so lookups by equality keep working). Run it with the same
ENCRYPTION_KEY the service will use; a different key makes the stored values unreadable.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23 06:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

FIELDS = {"email": ("email", 700), "phone": ("phone", 160), "nic": ("nic", 160), "account_number": ("account", 160)}
OLD = {"email": 255, "phone": 20, "nic": 20, "account_number": 32}


USERS = sa.table("users", sa.column("id"), sa.column("email"), sa.column("phone"), sa.column("nic"), sa.column("account_number"))


def _secret() -> str:
    from app.config import Settings

    return Settings.from_env().encryption_key


def upgrade() -> None:
    from app.crypto import check_key, seal_field

    secret = _secret()
    check_key(secret)  # stop before touching anything if the key is unusable
    with op.batch_alter_table("users", schema=None) as batch:
        for column, (_purpose, length) in FIELDS.items():
            batch.alter_column(column, existing_type=sa.String(OLD[column]), type_=sa.String(length), existing_nullable=column != "email")
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, email, phone, nic, account_number FROM users")).mappings().all()
    for row in rows:
        values = {}
        for column, (purpose, _length) in FIELDS.items():
            value = row[column]
            if value is not None and not str(value).startswith("siv1$"):
                values[column] = seal_field(str(value), secret, purpose)
        if values:
            bind.execute(sa.update(USERS).where(USERS.c.id == row["id"]).values(**values))


def downgrade() -> None:
    from app.crypto import open_field

    secret = _secret()
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, email, phone, nic, account_number FROM users")).mappings().all()
    for row in rows:
        values = {c: open_field(row[c], secret, p) for c, (p, _l) in FIELDS.items() if row[c] and str(row[c]).startswith("siv1$")}
        if values:
            bind.execute(sa.update(USERS).where(USERS.c.id == row["id"]).values(**values))
    with op.batch_alter_table("users", schema=None) as batch:
        for column, (_purpose, length) in FIELDS.items():
            batch.alter_column(column, existing_type=sa.String(length), type_=sa.String(OLD[column]), existing_nullable=column != "email")
