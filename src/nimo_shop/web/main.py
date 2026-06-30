from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from nimo_shop.web.app import create_server


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run NIMO Shop Web Admin")
    parser.add_argument("--host", default=os.getenv("WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT", "8080")))
    parser.add_argument("--db", default=os.getenv("DATABASE_PATH", "data/shop.db"))
    parser.add_argument("--username", default=os.getenv("WEB_ADMIN_USERNAME", "admin"))
    parser.add_argument("--password", default=os.getenv("WEB_ADMIN_PASSWORD") or None)
    args = parser.parse_args()
    server = create_server(
        args.db,
        host=args.host,
        port=args.port,
        session_secret=os.getenv("WEB_SESSION_SECRET"),
        project_root=Path.cwd(),
        bootstrap_username=args.username,
        bootstrap_password=args.password,
    )
    print(f"NIMO Web Admin running on http://{args.host}:{args.port}")
    print("Default login is admin / admin12345 if WEB_ADMIN_PASSWORD is not set. Change it before opening outside trusted LAN.")
    server.serve_forever()


if __name__ == "__main__":
    main()
