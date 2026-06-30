from __future__ import annotations

import os
from pathlib import Path

from nimo_shop.db import Database
from nimo_shop.services.audit import AuditService


def main() -> None:
    db_path = Path(os.getenv("DATABASE_PATH", "data/shop.db"))
    db = Database(db_path)
    db.init()
    issues = AuditService(db).run()
    if not issues:
        print("AUDIT OK: no consistency issues found")
        return
    print(f"AUDIT FAILED: {len(issues)} issue(s)")
    for issue in issues:
        print(f"- {issue.code}: {issue.message}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
