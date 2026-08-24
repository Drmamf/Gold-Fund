# FINAL RELEASE NOTES — 2026-08-15

این پوشه نسخه مرجع و بکاپ نهایی پروژه Wallex Gold Fund تا پایان تغییرات لایو 2026-08-15 است.

## اصلاحات ادغام‌شده

1. **Weekend TGJU Ounce Fix**
   - شنبه و یکشنبه، اگر بازار جهانی بسته باشد، آخرین اونس معتبر جمعه تا سقف 72 ساعت قابل استفاده است.
   - وضعیت داخلی: `MARKET_CLOSED_CARRY_FORWARD`.
   - freshness عادی اونس همچنان 15 دقیقه است.
   - عبور از سقف 72 ساعت دوباره `STALE_PRICE` واقعی تولید می‌کند.

2. **Runtime State Duplicate Fix**
   - Strategy A و Strategy B پس از ساخت state جدید `session.flush()` می‌کنند.
   - خطای unique مربوط به `GLOBAL/account` رفع شده است.

3. **Strategy B Bale Threshold Notification Fix**
   - تمام صندوق‌های عبورکرده از Buy Threshold همچنان در DB ثبت می‌شوند.
   - فقط بهترین کاندیدا با کمترین `Total Bubble` در بله به‌عنوان ورود منتخب نمایش داده می‌شود.

4. **Talagram Daily Asset Composition Sidecar**
   - ساعت 12:00 به وقت تهران فقط یک‌بار `https://talagram.org/box-assets` خوانده می‌شود.
   - هر 10 صندوق باید معتبر و حاضر باشند؛ در غیر این صورت Update رد می‌شود.
   - فقط `coin_ratio` و `shemsh_ratio` وارد مدل طلای پروژه می‌شوند و روی مبنای مجموع شمش+سکه نرمال می‌شوند.
   - داده روز در PostgreSQL (`asset_composition_history`) و CSV خارجی پروژه ثبت می‌شود.
   - Warm-up ساعت 12:03 بدون تغییر در موتور اصلی، آخرین ترکیب ثبت‌شده را استفاده می‌کند.
   - در طول روز Talagram دوباره Fetch نمی‌شود.
   - Systemd sidecar:
     - `wallex-gold-composition-update.service`
     - `wallex-gold-composition-update.timer`

5. **خاموش‌کردن Reminder قدیمی ترکیب دارایی**
   - Monitor ماهانه قدیمی از طریق Config غیرفعال شده است تا همزمان با Updater ساعت 12:00 هشدار اشتباه ارسال نکند.
   - کل تنظیمات قدیمی صندوق‌ها برای Audit در `legacy_funds` حفظ شده‌اند.
   - `funds: {}` است، بنابراین Monitor قدیمی هیچ Reminder بله‌ای تولید نمی‌کند.
   - هشدار واقعی خطای Talagram Updater همچنان فعال است.

## سیاست عملیاتی نهایی ترکیب دارایی

```text
12:00 Tehran
Talagram Sidecar -> Validate 10/10 -> Normalize -> PostgreSQL + CSV

12:03 Tehran
Main Bot Warm-up -> latest asset_composition_history for the trading date

12:05-17:00
Main cycles use the same daily composition; no repeated Talagram fetch.
```

## فایل‌های Core که برای Sidecar تغییر نکرده‌اند

- `app/pipeline.py`
- `app/valuation_engine.py`
- `app/relative_value_engine.py`
- `app/scheduler.py`
- `app/strategies/*`
- `app/execution/*`

## نکات امنیتی / آرشیوی

- `.env` واقعی عمداً داخل ZIP نیست.
- `.venv`, `logs`, `output`, `runtime_state`, `__pycache__` عمداً داخل ZIP نیستند.
- هنگام Restore روی VPS، `.env` واقعی موجود را نگه دارید و با `.env.example` جایگزین نکنید.
