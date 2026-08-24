# از اینجا شروع کن — Wallex Gold Fund

این پوشه **کل پروژه موردنیاز برای اجرای ربات روی VPS** است. برای اجرای اولیه لازم نیست فایل Python را تغییر بدهی.

## تصویر خیلی ساده از پروژه

ربات در هر چرخه این مسیر را طی می‌کند:

```text
APIهای بازار
   ↓
Collector (جمع‌آوری داده)
   ↓
Valuation (محاسبه حباب‌ها)
   ↓
Relative Value (مقایسه صندوق‌ها)
   ↓
Strategy A / Strategy B (تصمیم و Signal)
   ↓
Executor (اعمال روی حساب فرضی)
   ↓
PostgreSQL (ثبت همه چیز)
   ↓
Bale / CSV / Backup (گزارش‌دهی)
```

## زمان‌بندی

```text
شنبه تا چهارشنبه
12:00  دو پیام وضعیت حساب‌های فرضی روز قبل
12:03  Warm-up و اولین دریافت/محاسبه، بدون Signal و معامله
12:05  شروع کار عملیاتی و اولین چرخه Signal/Execution
هر 3 دقیقه تا 16:59
17:00  پایان پایش، دو گزارش حساب + CSV سیگنال‌های روز
چهارشنبه 18:00  ZIP بکاپ کل دیتابیس (یک CSV برای هر جدول)
```

پنجشنبه و جمعه چرخه بازار اجرا نمی‌شود.

## قانون مهم قیمت

برای محاسبه و امکان‌سنجی خرید **فقط ارزان‌ترین فروشنده (Best Ask)** معتبر است:

- صندوق طلا: Best Ask از TSETMC
- شمش: Best Ask از IME
- سکه: Best Ask از IME
- NAV: فقط NAV ابطال (`pRedTran`) از TSETMC

Mid، Last، Close و Settlement جایگزین محسوب نمی‌شوند.

برای فروش واقعی حساب فرضی، قیمت قابل فروش **Best Bid** است.

## قبل از انتقال به VPS

1. ZIP را روی Desktop Extract کن.
2. کل پوشه را بدون حذف فایل‌ها نگه دار.
3. فایل `.env.example` نمونه است؛ **Secret واقعی داخل ZIP نیست**.
4. روی VPS فایل `.env` از روی `.env.example` ساخته می‌شود و باید این موارد را وارد کنی:
   - `POSTGRES_PASSWORD`
   - `DATABASE_URL`
   - `BALE_BOT_TOKEN`
   - `BALE_CHAT_ID`
5. فایل‌های Python و YAML را تا قبل از Deploy بی‌دلیل تغییر نده.

## روی VPS چه می‌کنیم؟

بعد از انتقال پوشه:

```bash
cd /path/to/wallex_gold_project_COMPLETE
chmod +x scripts/install_vps.sh
./scripts/install_vps.sh
```

بار اول installer اگر `.env` وجود نداشته باشد، آن را می‌سازد و متوقف می‌شود. Secretها را پر می‌کنیم و installer را دوباره اجرا می‌کنیم.

بعد از نصب، قبل از اعتماد به داده زنده:

```bash
source .venv/bin/activate
python scripts/smoke_live.py
```

خروجی مطلوب:

```text
COMMON usable: True
VALID GOLD FUND VALUATIONS: 10/10
```

## فایل‌هایی که اول باید بخوانی

1. `00_START_HERE_FA.md` — همین فایل
2. `01_FILE_GUIDE_FA.md` — توضیح مبتدی تمام فایل‌ها
3. `DEPLOY_VPS.md` — مراحل انتقال و نصب روی VPS
4. `FINAL_CHECKLIST.md` — چک‌لیست قبل از روشن کردن سرویس

## نکته امنیتی

هیچ توکن یا پسورد واقعی را داخل Python، YAML یا فایل‌هایی که برای دیگران می‌فرستی قرار نده. Secretها فقط در `.env` روی VPS باشند.
