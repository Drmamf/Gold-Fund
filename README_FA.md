# Wallex Gold Fund — VPS Database + Unified Architecture

این بسته «اسکلت دیتابیس و معماری» پروژه است؛ منطق نهایی Collector/Valuation/Relative/Executor
در مراحل بعد به همین Interfaces متصل می‌شود.

## معماری

```text
TGJU / IME / TSETMC
        |
        v
   Shared Collector
        |
        +--> market_cycles
        +--> common_market_snapshot
        +--> fund_market_snapshot
        |
        v
 Shared Valuation Engine
        |
        +--> fund_valuation_snapshot
        |
        v
 Shared Relative Value Engine
        |
        +--> relative_value_snapshot
        |
   +----+--------------------+
   |                         |
   v                         v
Strategy A                Strategy B
RELATIVE_BUY_HOLD         THRESHOLD_10_10_RELATIVE
   |                         |
   +----------+--------------+
              |
              v
          signals
       (always first)
              |
              v
       Shared Executor
              |
     +--------+---------+
     |                  |
transactions       position_events
     |                  |
     +--------+---------+
              |
       positions_current
              |
       account_snapshots

Every cycle:
  daily_common_summary and daily_fund_summary are UPSERTed.
```

## 20 tables

1. instruments
2. asset_composition_history
3. config_versions
4. market_cycles
5. common_market_snapshot
6. fund_market_snapshot
7. fund_valuation_snapshot
8. relative_value_snapshot
9. daily_common_summary
10. daily_fund_summary
11. signals
12. transactions
13. positions_current
14. position_events
15. account_snapshots
16. strategy_runtime_state
17. asset_report_status
18. data_errors
19. notification_log
20. bot_runs

## VPS setup — Docker PostgreSQL

```bash
cd wallex_gold_vps_architecture
cp .env.example .env
```

یک پسورد قوی داخل `.env` و در متغیر `POSTGRES_PASSWORD` محیط Docker قرار بده.
سپس:

```bash
docker compose up -d db

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/init_db.py
python scripts/check_db.py
```

اگر PostgreSQL را خارج Docker نصب کرده‌ای، فقط `DATABASE_URL` را به دیتابیس همان VPS اشاره بده
و `docker compose` لازم نیست.


## قرارداد واحدها

داخل PostgreSQL و آبجکت‌های محاسباتی، تمام Bubble/Edge/Returnها به صورت **fraction** ذخیره می‌شوند:

```text
-1.10%  -> -0.011
+0.50%  -> +0.005
+2.80%  -> +0.028
```

فایل‌های YAML برای خوانایی درصد انسانی/percentage-points نگه می‌دارند.
تبدیل فقط در لایه Config/Units انجام می‌شود (`app/units.py`)؛ هیچ Strategy مجاز نیست
به شکل پراکنده `*100` یا `/100` انجام دهد.

## نکات طراحی

- Secret داخل Python نیست.
- Configهای استراتژی بیرون کد هستند.
- ترکیب دارایی ماهانه Versioned است؛ ردیف قبلی overwrite نمی‌شود.
- Raw Market Data از Derived Valuation جداست.
- Relative Engine فقط یک بار در هر cycle اجرا می‌شود.
- Strategyها اجازه Fetch مجدد داده بازار ندارند.
- Signal قبل از Account/Capacity check ذخیره می‌شود.
- Account A و B مستقل‌اند.
- `strategy_runtime_state` برای Restart امن VPS است.
- Daily Summary در هر cycle UPSERT می‌شود، نه فقط ساعت 17.
- `old_intrinsic_bubble` و `old_total_bubble` موقتاً برای Forward comparison نگه داشته شده‌اند.


## موتور مشترک Relative Value

پیاده‌سازی واقعی موتور در `app/relative_value_engine.py` است.

تعریف اصلی:

```text
current_gap_i = total_bubble_ayyar - total_bubble_i
relative_score_i = current_gap_i - historical_normal_gap_i

gross_edge(A -> B) = score_B - score_A
net_edge(A -> B) = gross_edge - direct_bid_ask_and_fee_cost
```

- عیار Anchor محاسباتی است، نه مسیر اجباری معامله.
- همه زوج‌های مستقیم fund→fund بررسی می‌شوند.
- مقصد بر اساس بیشترین `net_executable_edge` انتخاب می‌شود.
- هزینه اجرا از best bid مبدأ، best ask مقصد و کارمزد خرید/فروش محاسبه می‌شود.
- Engine فقط فرصت بازار را محاسبه می‌کند؛ حداقل Edge مثل 0.50pp در Strategyها اعمال می‌شود.
- Baseline تاریخی در `config/relative_value.yaml` است و Hard-code نشده.
- تمام Pairهای بررسی‌شده در `relative_value_snapshot.details` قابل Audit هستند.

تست:

```bash
python -m unittest tests.test_relative_value_engine -v
```

## Strategy A — نسخه اجرایی

Strategy A دیگر فقط Scaffold نیست. بخش‌های زیر پیاده‌سازی شده‌اند:

- Bootstrap حساب فرضی با 1 میلیارد ریال و خرید اولیه عیار
- شرط Rotation روی `net_executable_edge >= 0.50pp`
- فروش کل مبدأ روی Best Bid و خرید حداکثری مقصد روی Best Ask
- کارمزد خرید و فروش واقعی Paper Account
- حفظ یک `position_id` در تمام Rotationها
- ثبت Transaction / Position Event / Account Snapshot / Runtime State
- Mark-to-market در تمام Cycleها حتی HOLD
- Fail-safe برای Order Book و داده نامعتبر
- بازیابی State بعد از Restart VPS

مستند کامل: `STRATEGY_A.md`


## Strategy B — staged threshold entries + one-time MA7 Entry #2 fallback

نسخه نهایی در `STRATEGY_B.md` مستند شده است. ورودهای عادی 10٪ می‌توانند Entry 1/2/3/4/... باشند، اما هر ورود بعدی فقط پس از rearm +1.50pp پله قبلی مجاز است. MA7 فقط در صورت rearm نشدن Entry #1 می‌تواند یک Entry #2 جایگزین ایجاد کند و برای Entry #3 به بعد هرگز استفاده نمی‌شود.


## زمان‌بندی قطعی بازار

تمام زمان‌ها با `Asia/Tehran` تفسیر می‌شوند.

```text
روزهای فعال: شنبه تا چهارشنبه

12:03  WARMUP
        Shared Collector + Valuation + Relative + DB/Daily
        بدون Signal / Execution / Account mutation

12:05  شروع ACTIVE
        Shared Core
        Strategy A/B
        Signals
        Execution
        Account snapshots

هر 180 ثانیه تا قبل از 17:00
آخرین سیکل ACTIVE: 16:59

17:00  CLOSE
        آخرین Shared Snapshot/Calculation
        Mark-to-market حساب‌ها + Daily summaries
        بدون Signal جدید و بدون معامله جدید

بعد از 17:00 و پنجشنبه/جمعه هیچ سیکلی اجرا نمی‌شود.
```


## اعلان‌های بله

کد بله در `app/notifications/` قرار دارد. برنامه:

```text
12:00  دو کارت وضعیت حساب روز قبل
12:03  Warm-up بدون پیام
12:05  پیام شروع کار + اولین Active Cycle
روز     فقط Signalها + هشدار هر API failure
17:00  دو کارت پایان روز + daily signals CSV
Wed 18  Full database ZIP (one CSV per table)
```

جزئیات در `BALE_NOTIFICATIONS.md`.


## Shared Collector — سیاست قیمت قابل اجرا

از V4 هیچ Mid/Last/Close/Settlement fallback برای Valuation معتبر نیست.

```text
Gold Fund valuation price = TSETMC Best Ask فقط
IME GoldBar price          = IME Best Ask فقط
IME GoldCoin price         = IME Best Ask فقط
Gold Fund NAV              = TSETMC pRedTran فقط
```

اگر Best Ask یا NAV لازم وجود نداشته باشد، Snapshot همان ابزار Invalid است و
سیگنال valuation-based جدید تولید نمی‌شود.

Best Bid فقط برای عملیات واقعی فروش/Rotation نگه داشته می‌شود.

فایل‌های اصلی:
- `app/config_loader.py`
- `app/providers/tsetmc_adapter.py`
- `app/providers/ime_adapter.py`
- `app/collector.py`
- `config/instruments.yaml`
