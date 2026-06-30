from __future__ import annotations

from nimo_shop.config import Settings
from nimo_shop.db import Database
from nimo_shop.services.catalog import CatalogService


def seed_demo(db: Database) -> None:
    db.init()
    catalog = CatalogService(db)
    existing = catalog.list_categories()
    if existing:
        print("Database already has categories; skip demo seed.")
        return
    chatgpt = catalog.add_category("🤖 ChatGPT")
    gemini = catalog.add_category("💎 Gemini")
    canva = catalog.add_category("🎨 Canva")
    p1 = catalog.add_product(
        category_id=chatgpt,
        name="ChatGPT Plus 1 tháng",
        description="Tài khoản/gói premium dùng 30 ngày. Giao tự động sau khi thanh toán.",
        currency="VND",
        price_minor=150_000,
        cost_minor=100_000,
        warranty_text="1 đổi 1 trong thời gian bảo hành nếu lỗi do shop.",
    )
    p2 = catalog.add_product(
        category_id=gemini,
        name="Gemini Advanced 1 tháng",
        description="Gói Gemini Advanced dùng 30 ngày.",
        currency="VND",
        price_minor=120_000,
        cost_minor=80_000,
        warranty_text="Bảo hành theo chính sách shop.",
    )
    p3 = catalog.add_product(
        category_id=canva,
        name="Canva Pro 1 tháng",
        description="Gói Canva Pro, giao thông tin sử dụng ngay sau khi thanh toán.",
        currency="VND",
        price_minor=50_000,
        cost_minor=25_000,
        warranty_text="Bảo hành trong thời hạn gói.",
    )
    catalog.add_stock(p1, ["demo-chatgpt-1|password", "demo-chatgpt-2|password"])
    catalog.add_stock(p2, ["demo-gemini-1|password"])
    catalog.add_stock(p3, ["demo-canva-1|password", "demo-canva-2|password"])
    print("Seeded demo categories/products/stock.")


def main() -> None:
    settings = Settings.from_env()
    seed_demo(Database(settings.database_path))


if __name__ == "__main__":
    main()
