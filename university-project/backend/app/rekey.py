"""Change ENCRYPTION_KEY without losing the data it protects.

    OLD_ENCRYPTION_KEY=... NEW_ENCRYPTION_KEY=... DATABASE_URL=... python -m app.rekey [--dry-run]

Stop the service and take a database backup first. Every encrypted value is read with the old key and written with the
new one inside one transaction, then read back with the new key to prove it; if anything fails nothing is changed. The
keys come from the environment, not the command line, so they do not appear in the process list or the shell history.
Afterwards start the service with ENCRYPTION_KEY set to the new key."""
import os
import sys

import sqlalchemy as sa

from app.crypto import check_key, decrypt, encrypt, open_field, seal_field

# (table, columns, kind, purpose): "siv" is the deterministic field encryption, "gcm" the random-nonce one.
TARGETS = [
    ("users", "email", "siv", "email"),
    ("users", "phone", "siv", "phone"),
    ("users", "nic", "siv", "nic"),
    ("users", "account_number", "siv", "account"),
    ("users", "totp_secret", "gcm", "totp"),
    ("users", "face_ref", "gcm", "face"),
    ("sim_events", "phone", "siv", "phone"),
    ("notifications", "body", "gcm", "notification"),
    ("reports", "markdown", "gcm", "report"),
]


class RekeyError(RuntimeError):
    pass


def _open(kind: str, value: str, key: str, purpose: str) -> str:
    return open_field(value, key, purpose) if kind == "siv" else decrypt(value, key, purpose)


def _seal(kind: str, value: str, key: str, purpose: str) -> str:
    return seal_field(value, key, purpose) if kind == "siv" else encrypt(value, key, purpose)


def rotate(engine, old_key: str, new_key: str, dry_run: bool = False) -> dict:
    check_key(old_key)
    check_key(new_key)
    if old_key == new_key:
        raise RekeyError("The old and new keys are the same.")
    counts: dict[str, int] = {}
    connection = engine.connect()
    transaction = connection.begin()
    try:
        for table, column, kind, purpose in TARGETS:
            t = sa.table(table, sa.column("id"), sa.column(column))
            rows = connection.execute(sa.select(t.c.id, t.c[column]).where(t.c[column].is_not(None))).all()
            for row in rows:
                if row[1] == "":
                    continue
                try:
                    plain = _open(kind, row[1], old_key, purpose)
                except Exception as exc:
                    raise RekeyError(f"{table}.{column} row {row[0]} cannot be read with the old key: it is not the key these values were written with.") from exc
                sealed = _seal(kind, plain, new_key, purpose)
                if _open(kind, sealed, new_key, purpose) != plain:  # prove it before keeping it
                    raise RekeyError(f"{table}.{column} row {row[0]} did not read back with the new key.")
                connection.execute(sa.update(t).where(t.c.id == row[0]).values({column: sealed}))
                counts[f"{table}.{column}"] = counts.get(f"{table}.{column}", 0) + 1
        if dry_run:
            transaction.rollback()
        else:
            transaction.commit()
    except Exception:
        transaction.rollback()
        raise
    finally:
        connection.close()
    return counts


def main() -> int:
    old_key, new_key = os.environ.get("OLD_ENCRYPTION_KEY", ""), os.environ.get("NEW_ENCRYPTION_KEY", "")
    url = os.environ.get("DATABASE_URL", "")
    if not (old_key and new_key and url):
        print("Set OLD_ENCRYPTION_KEY, NEW_ENCRYPTION_KEY and DATABASE_URL in the environment.", file=sys.stderr)
        return 2
    dry = "--dry-run" in sys.argv
    engine = sa.create_engine(url)
    try:
        counts = rotate(engine, old_key, new_key, dry_run=dry)
    except Exception as exc:
        print(f"Nothing was changed. {exc}", file=sys.stderr)
        return 1
    print(("Dry run, nothing changed. Would re-encrypt: " if dry else "Re-encrypted: ") + (", ".join(f"{k} x{v}" for k, v in sorted(counts.items())) or "nothing"))
    if not dry:
        print("Start the service with ENCRYPTION_KEY set to the new key.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
