"""Entry point for the PayShield backend.

Run from the project/ directory:

    python main.py

or, with auto-reload during development:

    uvicorn app.main:app --reload --port 8000
"""
import os

import uvicorn


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
