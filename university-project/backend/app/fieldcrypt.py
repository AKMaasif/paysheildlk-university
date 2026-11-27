"""Start-up checks for the encrypted personal fields."""
import sqlalchemy as sa

from app.crypto import WeakKeyError, encrypt, seal_field

FIELDS = {"email": "email", "phone": "phone", "nic": "nic", "account_number": "account"}
USERS = sa.table("users", sa.column("id"), *[sa.column(name) for name in FIELDS])


def check_key_matches_data(engine, secret: str) -> None:
    """If the database already holds encrypted values, the configured key must open them. A wrong key would otherwise
    show up later as a failed login, a missing authenticator code or a crash in the middle of a request. One stored value
    of every kind is tried: personal fields, authenticator secrets, face data, notification and report text."""
    from app.rekey import TARGETS, _open

    with engine.connect() as connection:
        for table, column, kind, purpose in TARGETS:
            t = sa.table(table, sa.column(column))
            prefix = "siv1$%" if kind == "siv" else "gcm%"
            value = connection.execute(sa.select(t.c[column]).where(t.c[column].like(prefix)).limit(1)).scalar()
            if value is None:
                continue
            try:
                _open(kind, value, secret, purpose)
            except Exception as exc:
                raise WeakKeyError(
                    f"ENCRYPTION_KEY does not match the key this database was encrypted with (checked {table}.{column}). "
                    "Use the original key, or, for demonstration data you can lose, start fresh with `docker compose down -v`. "
                    "To move to a new key without losing data, use `python -m app.rekey`."
                ) from exc


SIM = sa.table("sim_events", sa.column("id"), sa.column("phone"))
NOTES = sa.table("notifications", sa.column("id"), sa.column("body"))
REPORTS = sa.table("reports", sa.column("id"), sa.column("markdown"))


def encrypt_plain_fields(engine, secret: str) -> int:
    """Encrypt any personal field still stored as plain text (a restored older backup, a row inserted by hand).
    Returns how many rows were changed. Safe to run on every start."""
    changed = 0
    with engine.begin() as connection:
        rows = connection.execute(sa.select(USERS)).mappings().all()
        for row in rows:
            values = {c: seal_field(str(row[c]), secret, p) for c, p in FIELDS.items() if row[c] is not None and not str(row[c]).startswith("siv1$")}
            if values:
                connection.execute(sa.update(USERS).where(USERS.c.id == row["id"]).values(**values))
                changed += 1
        # Telecom event numbers, notification text and report text. Only rows not yet encrypted are read.
        for row in connection.execute(sa.select(SIM.c.id, SIM.c.phone).where(SIM.c.phone.notlike("siv1$%"))).all():
            connection.execute(sa.update(SIM).where(SIM.c.id == row.id).values(phone=seal_field(str(row.phone), secret, "phone")))
            changed += 1
        for table, column, purpose in ((NOTES, "body", "notification"), (REPORTS, "markdown", "report")):
            for row in connection.execute(sa.select(table.c.id, table.c[column]).where(table.c[column].notlike("gcm2$%"))).all():
                connection.execute(sa.update(table).where(table.c.id == row.id).values({column: encrypt(str(row[1]), secret, purpose)}))
                changed += 1
    return changed
