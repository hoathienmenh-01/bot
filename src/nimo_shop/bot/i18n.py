from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_LANGUAGES: dict[str, tuple[str, str, str]] = {
    "vi": ("🇻🇳", "Tiếng Việt", "Vietnamese"),
    "en": ("🇺🇸", "English", "English"),
    "zh": ("🇨🇳", "中文", "Chinese"),
    "ja": ("🇯🇵", "日本語", "Japanese"),
    "ko": ("🇰🇷", "한국어", "Korean"),
    "th": ("🇹🇭", "ไทย", "Thai"),
    "es": ("🇪🇸", "Español", "Spanish"),
    "fr": ("🇫🇷", "Français", "French"),
}

MENU_KEYS = [
    ["buy", "search"],
    ["profile", "history"],
    ["wallet", "support"],
    ["language"],
]

TEXT: dict[str, dict[str, str]] = {
    "vi": {
        "buy": "🛒 Mua ngay",
        "search": "🔎 Tìm sản phẩm",
        "profile": "👤 Hồ sơ",
        "history": "📜 Lịch sử mua",
        "wallet": "💰 Ví",
        "support": "💬 Hỗ trợ",
        "language": "🌐 Ngôn ngữ",
        "input_placeholder": "Chọn chức năng...",
        "welcome": "👋 Chào mừng bạn đến với <b>{shop_name}</b>\n\nBạn có thể mua gói premium/tài khoản/key, nạp ví, xem lịch sử mua và liên hệ hỗ trợ ngay trong bot.\n\n{wallet_block}\n\nVui lòng chọn chức năng bên dưới:",
        "wallet_block_title": "💰 <b>Số dư ví của bạn</b>",
        "no_balance": "Chưa có số dư",
        "language_updated": "✅ Đã cập nhật ngôn ngữ: <b>{language}</b>.",
    },
    "en": {
        "buy": "🛒 Buy now",
        "search": "🔎 Search products",
        "profile": "👤 Profile",
        "history": "📜 Purchase history",
        "wallet": "💰 Wallet",
        "support": "💬 Support",
        "language": "🌐 Language",
        "input_placeholder": "Choose an action...",
        "welcome": "👋 Welcome to <b>{shop_name}</b>\n\nYou can buy premium plans/accounts/keys, top up your wallet, view purchase history and contact support inside this bot.\n\n{wallet_block}\n\nPlease choose an action below:",
        "wallet_block_title": "💰 <b>Your wallet balance</b>",
        "no_balance": "No balance yet",
        "language_updated": "✅ Language updated: <b>{language}</b>.",
    },
    "zh": {
        "buy": "🛒 立即购买",
        "search": "🔎 搜索商品",
        "profile": "👤 个人资料",
        "history": "📜 购买记录",
        "wallet": "💰 钱包",
        "support": "💬 支持",
        "language": "🌐 语言",
        "input_placeholder": "请选择功能...",
        "welcome": "👋 欢迎来到 <b>{shop_name}</b>\n\n你可以购买高级服务/账号/key，充值钱包，查看订单并联系支持。\n\n{wallet_block}\n\n请选择下方功能：",
        "wallet_block_title": "💰 <b>钱包余额</b>",
        "no_balance": "暂无余额",
        "language_updated": "✅ 已更新语言：<b>{language}</b>。",
    },
    "ja": {
        "buy": "🛒 今すぐ購入",
        "search": "🔎 商品検索",
        "profile": "👤 プロフィール",
        "history": "📜 購入履歴",
        "wallet": "💰 ウォレット",
        "support": "💬 サポート",
        "language": "🌐 言語",
        "input_placeholder": "操作を選択...",
        "welcome": "👋 <b>{shop_name}</b> へようこそ\n\nプレミアムプラン/アカウント/keyを購入し、ウォレットに入金できます。\n\n{wallet_block}\n\n下のメニューから選択してください：",
        "wallet_block_title": "💰 <b>ウォレット残高</b>",
        "no_balance": "残高はありません",
        "language_updated": "✅ 言語を更新しました：<b>{language}</b>。",
    },
    "ko": {
        "buy": "🛒 바로 구매",
        "search": "🔎 상품 검색",
        "profile": "👤 프로필",
        "history": "📜 구매 내역",
        "wallet": "💰 지갑",
        "support": "💬 지원",
        "language": "🌐 언어",
        "input_placeholder": "기능 선택...",
        "welcome": "👋 <b>{shop_name}</b>에 오신 것을 환영합니다\n\n프리미엄/계정/key 구매, 지갑 충전, 구매 내역 확인이 가능합니다.\n\n{wallet_block}\n\n아래 기능을 선택하세요:",
        "wallet_block_title": "💰 <b>지갑 잔액</b>",
        "no_balance": "잔액 없음",
        "language_updated": "✅ 언어가 변경되었습니다: <b>{language}</b>.",
    },
    "th": {
        "buy": "🛒 ซื้อเลย",
        "search": "🔎 ค้นหาสินค้า",
        "profile": "👤 โปรไฟล์",
        "history": "📜 ประวัติการซื้อ",
        "wallet": "💰 กระเป๋าเงิน",
        "support": "💬 ช่วยเหลือ",
        "language": "🌐 ภาษา",
        "input_placeholder": "เลือกเมนู...",
        "welcome": "👋 ยินดีต้อนรับสู่ <b>{shop_name}</b>\n\nคุณสามารถซื้อแพ็กเกจ/บัญชี/key เติมเงิน และดูประวัติได้ในบอตนี้\n\n{wallet_block}\n\nกรุณาเลือกเมนูด้านล่าง:",
        "wallet_block_title": "💰 <b>ยอดเงินของคุณ</b>",
        "no_balance": "ยังไม่มียอดเงิน",
        "language_updated": "✅ เปลี่ยนภาษาแล้ว: <b>{language}</b>.",
    },
    "es": {
        "buy": "🛒 Comprar ahora",
        "search": "🔎 Buscar productos",
        "profile": "👤 Perfil",
        "history": "📜 Historial",
        "wallet": "💰 Billetera",
        "support": "💬 Soporte",
        "language": "🌐 Idioma",
        "input_placeholder": "Elige una opción...",
        "welcome": "👋 Bienvenido a <b>{shop_name}</b>\n\nPuedes comprar planes premium/cuentas/keys, recargar tu billetera y ver tu historial.\n\n{wallet_block}\n\nElige una opción:",
        "wallet_block_title": "💰 <b>Saldo de tu billetera</b>",
        "no_balance": "Sin saldo",
        "language_updated": "✅ Idioma actualizado: <b>{language}</b>.",
    },
    "fr": {
        "buy": "🛒 Acheter",
        "search": "🔎 Rechercher",
        "profile": "👤 Profil",
        "history": "📜 Historique",
        "wallet": "💰 Portefeuille",
        "support": "💬 Support",
        "language": "🌐 Langue",
        "input_placeholder": "Choisissez une action...",
        "welcome": "👋 Bienvenue chez <b>{shop_name}</b>\n\nVous pouvez acheter des comptes/keys, recharger votre portefeuille et consulter l'historique.\n\n{wallet_block}\n\nChoisissez une action ci-dessous :",
        "wallet_block_title": "💰 <b>Solde du portefeuille</b>",
        "no_balance": "Aucun solde",
        "language_updated": "✅ Langue mise à jour : <b>{language}</b>.",
    },
}


def normalize_lang(lang: str | None) -> str:
    return lang if lang in SUPPORTED_LANGUAGES else "vi"


def t(lang: str | None, key: str) -> str:
    lang = normalize_lang(lang)
    return TEXT.get(lang, TEXT["vi"]).get(key, TEXT["vi"].get(key, key))


def menu_rows(lang: str | None = "vi") -> list[list[str]]:
    lang = normalize_lang(lang)
    return [[t(lang, key) for key in row] for row in MENU_KEYS]


def menu_texts(key: str) -> set[str]:
    return {data[key] for data in TEXT.values() if key in data}


def language_display(lang: str) -> str:
    flag, native, english = SUPPORTED_LANGUAGES[normalize_lang(lang)]
    return f"{flag} {native}" if native == english else f"{flag} {native} / {english}"
