from __future__ import annotations

from dataclasses import dataclass

from nimo_shop.db import Database


@dataclass(frozen=True)
class AuditIssue:
    code: str
    message: str


class AuditService:
    """Database consistency checks for money, stock and orders.

    This is intentionally strict and read-only. Run it before opening the shop,
    after manual DB edits/imports, and after incidents to detect silent drift.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def run(self) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        with self.db.connect() as conn:
            # Wallet balance must equal the signed sum of wallet ledger entries.
            rows = conn.execute(
                """
                SELECT wb.user_id, wb.currency, wb.balance_minor,
                       COALESCE(SUM(CASE le.direction WHEN 'credit' THEN le.amount_minor ELSE -le.amount_minor END), 0) AS ledger_balance
                  FROM wallet_balances wb
                  LEFT JOIN ledger_entries le ON le.user_id=wb.user_id AND le.currency=wb.currency
                 GROUP BY wb.user_id, wb.currency
                """
            ).fetchall()
            for row in rows:
                if int(row["balance_minor"]) != int(row["ledger_balance"]):
                    issues.append(AuditIssue(
                        "wallet_ledger_mismatch",
                        f"user_id={row['user_id']} {row['currency']} balance={row['balance_minor']} ledger={row['ledger_balance']}",
                    ))

            # Awaiting orders must hold exactly quantity reserved stock lines.
            rows = conn.execute(
                """
                SELECT o.id, o.public_code, o.quantity,
                       COUNT(s.id) AS reserved_count
                  FROM orders o
                  LEFT JOIN stock_items s ON s.reserved_order_id=o.id AND s.status='reserved'
                 WHERE o.status='awaiting_payment'
                 GROUP BY o.id
                """
            ).fetchall()
            for row in rows:
                if int(row["reserved_count"]) != int(row["quantity"]):
                    issues.append(AuditIssue(
                        "awaiting_order_reserved_stock_mismatch",
                        f"order={row['public_code']} quantity={row['quantity']} reserved={row['reserved_count']}",
                    ))

            # Cancelled/refunded/delivered orders must not keep stock reserved.
            rows = conn.execute(
                """
                SELECT o.public_code, o.status, COUNT(s.id) AS reserved_count
                  FROM orders o
                  JOIN stock_items s ON s.reserved_order_id=o.id AND s.status='reserved'
                 WHERE o.status IN ('cancelled','refunded','delivered','paid')
                 GROUP BY o.id
                """
            ).fetchall()
            for row in rows:
                issues.append(AuditIssue(
                    "closed_order_has_reserved_stock",
                    f"order={row['public_code']} status={row['status']} reserved={row['reserved_count']}",
                ))

            # Delivered/refunded orders must have exactly quantity delivery rows.
            rows = conn.execute(
                """
                SELECT o.id, o.public_code, o.status, o.quantity, COUNT(d.id) AS delivery_count
                  FROM orders o
                  LEFT JOIN deliveries d ON d.order_id=o.id
                 WHERE o.status IN ('delivered','refunded')
                 GROUP BY o.id
                """
            ).fetchall()
            for row in rows:
                if int(row["delivery_count"]) != int(row["quantity"]):
                    issues.append(AuditIssue(
                        "delivered_order_delivery_mismatch",
                        f"order={row['public_code']} status={row['status']} quantity={row['quantity']} deliveries={row['delivery_count']}",
                    ))

            # Sold stock must be linked to a delivered/refunded order and delivery row.
            rows = conn.execute(
                """
                SELECT s.id, s.product_id, s.sold_order_id,
                       o.status AS order_status,
                       d.id AS delivery_id
                  FROM stock_items s
                  LEFT JOIN orders o ON o.id=s.sold_order_id
                  LEFT JOIN deliveries d ON d.stock_item_id=s.id
                 WHERE s.status='sold'
                """
            ).fetchall()
            for row in rows:
                if row["sold_order_id"] is None or row["order_status"] not in {"delivered", "refunded"} or row["delivery_id"] is None:
                    issues.append(AuditIssue(
                        "sold_stock_delivery_link_mismatch",
                        f"stock_id={row['id']} product_id={row['product_id']} sold_order_id={row['sold_order_id']} order_status={row['order_status']}",
                    ))

            # Available stock must not retain reservation/sale references.
            rows = conn.execute(
                """
                SELECT id, product_id FROM stock_items
                 WHERE status='available' AND (
                       reserved_by_user_id IS NOT NULL OR reserved_order_id IS NOT NULL OR reserved_until IS NOT NULL
                       OR sold_order_id IS NOT NULL OR sold_at IS NOT NULL
                 )
                """
            ).fetchall()
            for row in rows:
                issues.append(AuditIssue("available_stock_has_stale_refs", f"stock_id={row['id']} product_id={row['product_id']}"))

            # Cash ledger provider transactions should have matching provider events for external money in.
            rows = conn.execute(
                """
                SELECT cl.id, cl.provider, cl.idempotency_key
                  FROM cash_ledger cl
                 WHERE cl.direction='in' AND cl.provider NOT IN ('wallet')
                   AND NOT EXISTS (
                       SELECT 1 FROM external_payment_events e
                        WHERE cl.idempotency_key = 'cash:' || e.provider || ':' || e.provider_tx_id
                   )
                """
            ).fetchall()
            for row in rows:
                issues.append(AuditIssue("cash_ledger_missing_provider_event", f"cash_id={row['id']} provider={row['provider']} key={row['idempotency_key']}"))

        return issues
