"""python -m app.migrate new "what changed"   write the next migration script after you change a model
python -m app.migrate head                    print the newest revision"""
import sys

from app import migrate

if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["head"]:
        print(migrate.head_revision())
    elif args[:1] == ["new"] and len(args) == 2:
        path = migrate.new_revision(args[1])
        print(f"Wrote {path}. Read it, then run the tests." if path else "The models already match the migration scripts. Nothing to write.")
    else:
        print(__doc__)
        sys.exit(2)
