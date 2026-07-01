from __future__ import annotations

from nimo_shop.db import Database
from nimo_shop.payments.sepay import BankAccount

SUPPORTED_BANK_PROVIDERS = {"sepay", "pay2s", "casso", "custom", "manual"}


def normalize_bank_provider(value: str) -> str:
    provider = (value or "sepay").strip().lower()
    aliases = {
        "": "sepay",
        "none": "manual",
        "bank": "manual",
        "sepay_api": "sepay",
        "casso_api": "casso",
        "pay2s_api": "pay2s",
        "pay2s-token": "pay2s",
    }
    provider = aliases.get(provider, provider)
    if provider not in SUPPORTED_BANK_PROVIDERS:
        raise ValueError("provider phải là sepay, pay2s, casso, custom hoặc manual")
    return provider


class BankAccountService:
    """Manage multiple bank receiving accounts used for VietQR and polling.

    Direct bank APIs are not uniform across Vietnamese banks, so the runtime
    supports automatic polling for SePay and Pay2S accounts. Other
    providers are stored clearly for admin/manual/custom integration instead of
    pretending every bank API works with the same endpoint.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _clean(data: dict[str, str]) -> dict[str, object]:
        poll_raw = str(data.get("poll_seconds") or "30").strip()
        try:
            poll_seconds = max(5, int(poll_raw))
        except ValueError:
            poll_seconds = 30
        return {
            "label": str(data.get("label") or data.get("bank_name") or "Tài khoản ngân hàng").strip(),
            "bank_name": str(data.get("bank_name") or "").strip(),
            "bank_bin": str(data.get("bank_bin") or "").strip(),
            "account_no": str(data.get("account_no") or "").strip(),
            "account_name": str(data.get("account_name") or "").strip(),
            "provider": normalize_bank_provider(str(data.get("provider") or "sepay")),
            "api_key": str(data.get("api_key") or "").strip(),
            "api_secret": str(data.get("api_secret") or "").strip(),
            "base_url": str(data.get("base_url") or "").strip(),
            "poll_seconds": poll_seconds,
            "is_enabled": 1 if str(data.get("is_enabled") or "").lower() in {"1", "true", "on", "yes"} else 0,
            "is_default": 1 if str(data.get("is_default") or "").lower() in {"1", "true", "on", "yes"} else 0,
            "notes": str(data.get("notes") or "").strip(),
        }

    @staticmethod
    def _validate(clean: dict[str, object]) -> None:
        missing = []
        for key, label in (("label", "tên hiển thị"), ("bank_bin", "Bank BIN"), ("account_no", "số tài khoản"), ("account_name", "chủ tài khoản")):
            if not str(clean.get(key) or "").strip():
                missing.append(label)
        if missing:
            raise ValueError("Vui lòng nhập đủ " + ", ".join(missing))
        provider = str(clean.get("provider") or "manual")
        if provider in {"sepay", "pay2s"} and int(clean.get("is_enabled") or 0) and not str(clean.get("api_key") or "").strip():
            # Keep it enabled for VietQR/manual use, but do not throw. Admins may
            # want QR creation first and add API key later.
            pass

    def list_accounts(self, *, include_disabled: bool = True) -> list[dict]:
        sql = "SELECT * FROM bank_accounts"
        params: tuple[object, ...] = ()
        if not include_disabled:
            sql += " WHERE is_enabled=1"
        sql += " ORDER BY is_default DESC, is_enabled DESC, id DESC"
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def enabled_accounts(self) -> list[dict]:
        return self.list_accounts(include_disabled=False)

    def get(self, account_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM bank_accounts WHERE id=?", (account_id,)).fetchone()
        return dict(row) if row else None

    def default_account(self) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM bank_accounts WHERE is_enabled=1 ORDER BY is_default DESC, id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def create(self, data: dict[str, str], *, admin_id: int | None = None) -> int:  # admin_id kept for service API symmetry
        clean = self._clean(data)
        self._validate(clean)
        with self.db.transaction() as conn:
            if int(clean["is_default"]):
                conn.execute("UPDATE bank_accounts SET is_default=0")
            cur = conn.execute(
                """
                INSERT INTO bank_accounts(label, bank_name, bank_bin, account_no, account_name, provider, api_key, api_secret, base_url, poll_seconds, is_default, is_enabled, notes)
                VALUES(:label, :bank_name, :bank_bin, :account_no, :account_name, :provider, :api_key, :api_secret, :base_url, :poll_seconds, :is_default, :is_enabled, :notes)
                """,
                clean,
            )
            account_id = int(cur.lastrowid)
            row = conn.execute("SELECT COUNT(*) AS c FROM bank_accounts WHERE is_default=1 AND is_enabled=1").fetchone()
            if int(row["c"] or 0) == 0 and int(clean["is_enabled"]):
                conn.execute("UPDATE bank_accounts SET is_default=1 WHERE id=?", (account_id,))
            return account_id

    def update(self, account_id: int, data: dict[str, str], *, admin_id: int | None = None) -> None:
        existing = self.get(account_id)
        if not existing:
            raise ValueError("Không tìm thấy tài khoản ngân hàng")
        clean = self._clean(data)
        # Blank secret fields keep existing values, same behavior as settings.
        if not str(clean["api_key"]):
            clean["api_key"] = str(existing.get("api_key") or "")
        if not str(clean["api_secret"]):
            clean["api_secret"] = str(existing.get("api_secret") or "")
        self._validate(clean)
        clean["id"] = account_id
        with self.db.transaction() as conn:
            if int(clean["is_default"]):
                conn.execute("UPDATE bank_accounts SET is_default=0")
            conn.execute(
                """
                UPDATE bank_accounts
                   SET label=:label, bank_name=:bank_name, bank_bin=:bank_bin, account_no=:account_no,
                       account_name=:account_name, provider=:provider, api_key=:api_key, api_secret=:api_secret,
                       base_url=:base_url, poll_seconds=:poll_seconds, is_default=:is_default,
                       is_enabled=:is_enabled, notes=:notes, updated_at=CURRENT_TIMESTAMP
                 WHERE id=:id
                """,
                clean,
            )
            row = conn.execute("SELECT COUNT(*) AS c FROM bank_accounts WHERE is_default=1 AND is_enabled=1").fetchone()
            if int(row["c"] or 0) == 0:
                fallback = conn.execute("SELECT id FROM bank_accounts WHERE is_enabled=1 ORDER BY id DESC LIMIT 1").fetchone()
                if fallback:
                    conn.execute("UPDATE bank_accounts SET is_default=1 WHERE id=?", (int(fallback["id"]),))

    def delete(self, account_id: int, *, admin_id: int | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM bank_accounts WHERE id=?", (account_id,))
            row = conn.execute("SELECT COUNT(*) AS c FROM bank_accounts WHERE is_default=1 AND is_enabled=1").fetchone()
            if int(row["c"] or 0) == 0:
                fallback = conn.execute("SELECT id FROM bank_accounts WHERE is_enabled=1 ORDER BY id DESC LIMIT 1").fetchone()
                if fallback:
                    conn.execute("UPDATE bank_accounts SET is_default=1 WHERE id=?", (int(fallback["id"]),))

    def set_default(self, account_id: int, *, admin_id: int | None = None) -> None:
        if not self.get(account_id):
            raise ValueError("Không tìm thấy tài khoản ngân hàng")
        with self.db.transaction() as conn:
            conn.execute("UPDATE bank_accounts SET is_default=0")
            conn.execute("UPDATE bank_accounts SET is_default=1, is_enabled=1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (account_id,))

    @staticmethod
    def to_vietqr_bank(account: dict) -> BankAccount:
        return BankAccount(
            bank_bin=str(account.get("bank_bin") or ""),
            account_no=str(account.get("account_no") or ""),
            account_name=str(account.get("account_name") or ""),
            bank_name=str(account.get("bank_name") or account.get("label") or ""),
        )
