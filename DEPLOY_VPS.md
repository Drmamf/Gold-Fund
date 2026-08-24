# استقرار نهایی Wallex Gold Fund روی VPS

این نسخه برای Paper Trading طراحی شده و تمام اجزای Pipeline در یک پروژه قرار دارند.

## 1) انتقال پروژه

پروژه را روی VPS Extract کنید، مثلاً:

```bash
/opt/wallex-gold
```

## 2) تنظیم Secretها

```bash
cp .env.example .env
nano .env
```

فقط مقادیر واقعی زیر را وارد کنید:

```text
POSTGRES_PASSWORD
DATABASE_URL        # همان پسورد بالا در URL
BALE_BOT_TOKEN
BALE_CHAT_ID
```

Secret واقعی را داخل Python/YAML قرار ندهید.

## 3) نصب و اجرای یک‌مرحله‌ای

از ریشه پروژه:

```bash
chmod +x scripts/install_vps.sh
./scripts/install_vps.sh
```

این Script:

1. PostgreSQL را با Docker Compose بالا می‌آورد.
2. venv می‌سازد.
3. requirements را نصب می‌کند.
4. 20 جدول را می‌سازد/بررسی می‌کند.
5. 10 صندوق + آفران را seed می‌کند.
6. ترکیب دارایی فعلی را seed می‌کند.
7. preflight اجرا می‌کند.
8. systemd service می‌سازد.
9. سرویس `wallex-gold.service` را enable/start می‌کند.

## 4) بررسی وضعیت

```bash
sudo systemctl status wallex-gold.service
journalctl -u wallex-gold.service -f
tail -f logs/wallex_gold_bot.log
```

## 5) Smoke Test بازار زنده

این تست هیچ Signal/Execution ایجاد نمی‌کند و فقط API → Collector → Valuation را می‌سنجد.
بهتر است داخل ساعت بازار اجرا شود چون سیاست قیمت پروژه `Best Ask only` است:

```bash
source .venv/bin/activate
python scripts/smoke_live.py
```

خروجی مطلوب:

```text
COMMON usable: True
VALID GOLD FUND VALUATIONS: 10/10
```

اگر API یا Ask/NAV یکی از منابع نامعتبر باشد، تست Fail-Closed می‌شود؛ این رفتار عمدی است.

## زمان‌بندی نهایی

```text
شنبه تا چهارشنبه
12:00  یادآوری ترکیب دارایی (فقط اگر overdue) + دو پیام وضعیت حساب روز قبل
12:03  Warm-up: Collector + Valuation + Relative + DB، بدون Signal/Execution
12:05  پیام شروع + اولین Active Cycle
12:08..16:59 Active Cycle هر 180 ثانیه
17:00  Close snapshot، بدون Signal/Trade + دو گزارش حساب + Signals CSV
چهارشنبه 18:00 Full DB Backup ZIP
پنجشنبه/جمعه هیچ Market Cycle اجرا نمی‌شود
```

## سیاست قیمت نهایی

```text
خرید/Valuation صندوق = Best Ask TSETMC فقط
Valuation شمش         = Best Ask IME فقط
Valuation سکه         = Best Ask IME فقط
NAV                    = TSETMC Redemption pRedTran فقط
فروش                   = Best Bid فقط
```

هیچ Mid/Last/Close/Settlement/NAV fallback در Signal/Valuation وجود ندارد.

## به‌روزرسانی ترکیب دارایی ماهانه

وقتی گزارش جدید صندوق‌ها آماده شد، CSV زیر را با ردیف/نسخه جدید به‌روزرسانی کنید:

```text
config/fund_asset_composition_gold_normalized.csv
```

سپس:

```bash
source .venv/bin/activate
python scripts/init_db.py
```

رکورد تاریخی قبلی overwrite نمی‌شود. Reminder بله وقتی `as_of_date` دوره جدید ثبت شود خودکار متوقف می‌شود.
