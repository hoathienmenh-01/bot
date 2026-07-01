from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

from nimo_shop.config import Settings
from nimo_shop.db import Database
from nimo_shop.main import amain, is_configured_bot_token
from nimo_shop.web.app import create_server


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run NIMO Telegram bot and Web Admin in one process")
    parser.add_argument("--host", default=os.getenv("WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT", "8080")))
    parser.add_argument("--db", default=os.getenv("DATABASE_PATH", "data/shop.db"))
    parser.add_argument("--username", default=os.getenv("WEB_ADMIN_USERNAME", "admin"))
    parser.add_argument("--password", default=os.getenv("WEB_ADMIN_PASSWORD") or None)
    args = parser.parse_args()
    if not args.password:
        args.password = secrets.token_urlsafe(12)
    session_secret = os.getenv("WEB_SESSION_SECRET") or secrets.token_urlsafe(32)

    settings = Settings.from_env()
    db = Database(args.db)
    db.init()

    server = create_server(
        args.db,
        host=args.host,
        port=args.port,
        session_secret=session_secret,
        project_root=Path.cwd(),
        bootstrap_username=args.username,
        bootstrap_password=args.password,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="nimo-web-admin")
    thread.start()
    print(f"NIMO Web Admin đang chạy: http://127.0.0.1:{args.port}")
    print(f"Tài khoản setup: {args.username} / {args.password}")
    print("Hãy lưu WEB_ADMIN_PASSWORD và WEB_SESSION_SECRET cố định trong .env trước khi mở public.")
    if args.host not in {"127.0.0.1", "localhost"}:
        print(f"Mở từ máy khác cùng WiFi: http://<IP_MAY_CHAY_BOT>:{args.port}")

    try:
        if not is_configured_bot_token(settings.bot_token):
            print("BOT_TOKEN chưa đúng/trống. Hãy vào Web Admin → Cấu hình để nhập token, lưu .env rồi restart lệnh này.")
            print("Web Admin sẽ tiếp tục chạy. Bấm Ctrl+C để dừng.")
            while True:
                time.sleep(3600)
        asyncio.run(amain(setup_web_on_invalid_token=False))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
