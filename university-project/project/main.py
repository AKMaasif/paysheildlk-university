"""PayShield entry point.

Starts the fraud-desk API (FastAPI + LangGraph agents) on http://localhost:8000.

    pip install -r project/requirements.txt
    python project/main.py

The code itself stays in backend/app, so Docker Compose, the tests and the
frontend work exactly as before. This file only starts it from the folder the
course layout expects. Use --reload while developing, and --port or --host to
change where it listens. Environment variables are the same as in .env.example.
"""

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PayShield API")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="restart when backend code changes")
    args = parser.parse_args()

    # The database and checkpoint files are relative paths (./data/...), so run
    # from backend/ to keep using the same files as `uvicorn app.main:app`.
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(BACKEND / "app")] if args.reload else None,
    )


if __name__ == "__main__":
    main()
