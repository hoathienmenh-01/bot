from __future__ import annotations

from nimo_shop.db import Database


class FinanceService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def summary(self) -> dict:
        with self.db.connect() as conn:
            cash = [dict(r) for r in conn.execute(
                """
                SELECT currency, provider, direction,
                       SUM(amount_minor) AS amount_minor,
                       SUM(fee_minor) AS fee_minor,
                       COUNT(*) AS count
                  FROM cash_ledger
                 GROUP BY currency, provider, direction
                 ORDER BY currency, provider, direction
                """
            )]
            wallet = [dict(r) for r in conn.execute(
                """
                SELECT currency, SUM(balance_minor) AS liability_minor, COUNT(*) AS wallets
                  FROM wallet_balances GROUP BY currency ORDER BY currency
                """
            )]
            sales = [dict(r) for r in conn.execute(
                """
                SELECT o.currency, SUM(o.total_amount_minor) AS revenue_minor,
                       SUM(p.cost_minor * o.quantity) AS cost_minor,
                       COUNT(*) AS orders
                  FROM orders o JOIN products p ON p.id=o.product_id
                 WHERE o.status IN ('paid','delivered')
                 GROUP BY o.currency
                """
            )]
            pending = [dict(r) for r in conn.execute(
                "SELECT status, COUNT(*) AS count FROM orders GROUP BY status"
            )]
            return {"cash": cash, "wallet_liabilities": wallet, "sales": sales, "orders_by_status": pending}
