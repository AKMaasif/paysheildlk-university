"""encrypt_more_fields

Encrypts three more places that held personal details in plain text: the phone number on each telecom event
(sim_events.phone, same deterministic encryption as users.phone so equal numbers still match), the text of each
notification (notifications.body) and the text of each case report (reports.markdown). Run it with the same
ENCRYPTION_KEY the service will use.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-24 18:30:00
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SIM = sa.table("sim_events", sa.column("id"), sa.column("phone"))
NOTES = sa.table("notifications", sa.column("id"), sa.column("body"))
REPORTS = sa.table("reports", sa.column("id"), sa.column("markdown"))


def _secret() -> str:
    from app.config import Settings

    return Settings.from_env().encryption_key


def upgrade() -> None:
    from app.crypto import check_key, encrypt, seal_field

    secret = _secret()
    check_key(secret)
    with op.batch_alter_table("sim_events", schema=None) as batch:
        batch.alter_column("phone", existing_type=sa.String(20), type_=sa.String(160), existing_nullable=False)
    bind = op.get_bind()
    for row in bind.execute(sa.select(SIM.c.id, SIM.c.phone)).all():
        if row.phone is not None and not str(row.phone).startswith("siv1$"):
            bind.execute(sa.update(SIM).where(SIM.c.id == row.id).values(phone=seal_field(str(row.phone), secret, "phone")))
    for table, column, purpose in ((NOTES, "body", "notification"), (REPORTS, "markdown", "report")):
        for row in bind.execute(sa.select(table.c.id, table.c[column])).all():
            value = row[1]
            if value is not None and not str(value).startswith("gcm2$"):
                bind.execute(sa.update(table).where(table.c.id == row.id).values({column: encrypt(str(value), secret, purpose)}))


def downgrade() -> None:
    from app.crypto import decrypt, open_field

    secret = _secret()
    bind = op.get_bind()
    for row in bind.execute(sa.select(SIM.c.id, SIM.c.phone)).all():
        if row.phone and str(row.phone).startswith("siv1$"):
            bind.execute(sa.update(SIM).where(SIM.c.id == row.id).values(phone=open_field(row.phone, secret, "phone")))
    for table, column, purpose in ((NOTES, "body", "notification"), (REPORTS, "markdown", "report")):
        for row in bind.execute(sa.select(table.c.id, table.c[column])).all():
            if row[1] and str(row[1]).startswith("gcm2$"):
                bind.execute(sa.update(table).where(table.c.id == row.id).values({column: decrypt(row[1], secret, purpose)}))
    with op.batch_alter_table("sim_events", schema=None) as batch:
        batch.alter_column("phone", existing_type=sa.String(160), type_=sa.String(20), existing_nullable=False)
