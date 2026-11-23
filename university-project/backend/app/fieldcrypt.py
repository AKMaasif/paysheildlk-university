"""Start-up checks for the encrypted personal fields."""
import sqlalchemy as sa

from app.crypto import WeakKeyError, open_field, seal_field

FIELDS = {"email": "email", "phone": "phone", "nic": "nic", "account_number": "account"}
USERS = sa.table("users", sa.column("id"), *[sa.column(name) for name in FIELDS])


def check_key_matches_data(engine, secret: str) -> None:
    """If the database already holds encrypted fields, the configured key must open them. A wrong key would otherwise
    show up later as a failed login or a crash in the middle of a request."""
    with engine.connect() as connection:
        for column, purpose in FIELDS.items():
            value = connection.execute(sa.select(USERS.c[column]).where(USERS.c[column].like("siv1$%")).limit(1)).scalar()
            if value is not None:
                try:
                    open_field(value, secret, purpose)
                except Exception as exc:
                    raise WeakKeyError("ENCRYPTION_KEY does not match the key this database was encrypted with. Use the original key; changing it makes stored personal data unreadable.") from exc
                return


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
    return changed
