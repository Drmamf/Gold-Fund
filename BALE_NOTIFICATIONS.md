# Bale Notifications — Wallex Gold Fund

## برنامه پیام‌ها

- `12:00` دو پیام جدا: آخرین وضعیت حساب Strategy A و Strategy B از روز قبل.
- `12:03` Warm-up بدون پیام.
- `12:05` پیام شروع تایم کاری؛ سپس اولین Cycle عملیاتی.
- طی روز: فقط Signalهای Strategy A/B.
- هر API fetch ناموفق: یک هشدار با منبع/عملیات/صندوق/Endpoint/خطا.
- `17:00` دو پیام وضعیت نهایی حساب + فایل CSV تمام Signalهای روز.
- چهارشنبه `18:00`: ZIP کامل دیتابیس، یک CSV برای هر جدول.

## نکته جداسازی Signal و Execution

پیام‌های داخل روز فقط Signal هستند. پیام Signal قبل از Executor ارسال می‌شود و
نباید با «معامله انجام شد» اشتباه شود. نتیجه اجرای حساب در دیتابیس و CSV روزانه
ثبت می‌شود.

## جلوگیری از تکرار سوییچ Strategy B

اعلان Bale برای سوییچ Strategy B با کلید زیر شناخته می‌شود:

```text
position_id + source_fund_id + target_fund_id
```

- تا وقتی همین فرصت به‌صورت پیوسته برقرار است، فقط یک‌بار به Bale اعلام می‌شود.
- تغییر Source یا Target یک فرصت جدید است و فوراً قابل اعلان است.
- اگر فرصت در یک Cycle فعال وجود نداشته باشد، همان کلید re-arm می‌شود و در صورت
  برگشت دوباره قابل اعلان است.
- در شروع هر روز معاملاتی state اعلان reset می‌شود.
- این state فقط مربوط به Notification است و cooldown فعلی ذخیره/اجرای Signal را
  تغییر نمی‌دهد.

## API Error Alerts

در Shared Collector هر logical provider fetch باید با `ProviderCallGuard` اجرا شود:

```python
result = guard.call(
    source="TGJU",
    operation="fetch_market_snapshot",
    endpoint="https://call2.tgju.org/ajax.json",
    fn=tgju.fetch_market_snapshot,
)
```

برای TSETMC می‌توان `instrument_symbol` را هم ارسال کرد. هیچ dedup یا suppression
روی API warning وجود ندارد؛ اگر یک provider در دو Cycle متوالی fail شود، دو هشدار
مجزا ارسال می‌شود.

## Secrets

توکن و Chat ID فقط در `.env`:

```text
BALE_BOT_TOKEN=...
BALE_CHAT_ID=...
```

هیچ Secretی در Python/YAML ذخیره نشود.
