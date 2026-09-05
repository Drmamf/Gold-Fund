from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from app.live.notify import notify_ops


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

TEXT = """⚡️ ترید واقعی Strategy A روی کارآمد راه‌اندازی شد.

از این به بعد هر سیگنالی که روی حساب واقعی اجرا شود، همین‌جا هم اعلام می‌شود.

سقف سرمایه درگیر: ۵۰ میلیون تومان
حساب: کارآمد / کارگزاری کارآمد
بازار: شنبه تا چهارشنبه ۱۲:۰۰ تا ۱۸:۰۰ تهران
بات کاغذی جداگانه و بدون تغییر ادامه دارد. Strategy B زنده نیست.

الان ورکر لایو روشن است. تا وقتی dry-run فعال باشد فرم سفارش پر می‌شود ولی کلیک خرید/فروش زده نمی‌شود.
"""


def main() -> int:
    notify_ops(TEXT.strip())
    print("announcement_sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
