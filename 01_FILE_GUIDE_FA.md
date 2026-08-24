# راهنمای مبتدی فایل‌های پروژه

این فایل برای توضیح پروژه به کسی نوشته شده که لزوماً برنامه‌نویس نیست.

---

## فایل‌های ریشه پروژه

### `.env.example`
نمونه فایل اطلاعات محرمانه است. پسورد دیتابیس و توکن بله در نسخه واقعی داخل فایل `.env` روی VPS قرار می‌گیرند. خود `.env` داخل ZIP نیست.

### `.gitignore`
می‌گوید فایل‌های محرمانه و موقت مثل `.env`، لاگ‌ها و محیط مجازی Python وارد Git/بسته‌های اشتراکی نشوند.

### `requirements.txt`
فهرست کتابخانه‌های Python موردنیاز پروژه است؛ installer آن‌ها را نصب می‌کند.

### `docker-compose.yml`
PostgreSQL را داخل Docker بالا می‌آورد. دیتابیس اصلی پروژه در همین سرویس نگهداری می‌شود.

### `README_FA.md`
توضیح فنی کلی معماری پروژه.

### `DEPLOY_VPS.md`
راهنمای نصب روی VPS.

### `FINAL_CHECKLIST.md`
چک‌لیست نهایی قبل از روشن کردن سرویس.

### `PROJECT_TREE.txt`
فهرست ساختار فایل‌های پروژه.

### `STRATEGY_A.md`
توضیح کامل Strategy A: حسابی که ابتدا عیار دارد و با Relative Value بین صندوق‌ها سوییچ می‌کند.

### `STRATEGY_B.md`
توضیح کامل Strategy B: ورودهای 10 درصدی آستانه‌ای، Rearm 1.5pp، MA7 فقط به‌عنوان fallback ورود دوم، خروج و Relative Rotation.

### `BALE_NOTIFICATIONS.md`
توضیح پیام‌های بله، هشدار API، فایل روزانه Signal و بکاپ هفتگی.

---

# پوشه `config/` — تنظیمات قابل تغییر بدون دست زدن به Python

### `config/app.yaml`
ساعت‌ها و روزهای کاری را مشخص می‌کند: 12:00، 12:03، 12:05، 17:00 و بکاپ چهارشنبه 18:00.

### `config/market_config.yaml`
آدرس APIهای TGJU/TSETMC/IME، timeout/retry، قانون Best Ask only و ثابت‌های محاسبه طلای خالص را نگه می‌دارد.

### `config/instruments.yaml`
لیست 10 صندوق طلا + آفران و شناسه‌های TSETMC/ISIN آن‌هاست.

### `config/relative_value.yaml`
Baseline تاریخی Relative Value، Anchor عیار و کارمزد خرید/فروش را نگه می‌دارد.

### `config/strategy_a.yaml`
تنظیمات Strategy A: سرمایه اولیه، تک‌پوزیشن، حداقل Net Edge برابر 0.50pp و شیوه اجرا.

### `config/strategy_b.yaml`
تنظیمات Strategy B: سرمایه اولیه، ورود 10٪، Rearm 1.50pp، MA7، سقف 30٪ هر صندوق، سقف 100٪ طلا، آفران و Thresholdهای خرید/فروش.

### `config/fund_asset_composition_gold_normalized.csv`
آخرین وزن نرمال‌شده شمش و سکه برای هر صندوق را نگه می‌دارد. Valuation Engine از این وزن‌ها برای Intrinsic Bubble استفاده می‌کند.

### `config/fund_asset_composition_report_schedule.yaml`
زمان مورد انتظار گزارش ترکیب دارایی هر صندوق را تعیین می‌کند. برای زر دوره روز 14 است و بقیه پایان ماه؛ اگر به‌روزرسانی انجام نشده باشد بله یادآوری می‌کند.

---

# پوشه `app/` — مغز اصلی ربات

### `app/main.py`
نقطه شروع کل برنامه است. تمام قطعات را به هم وصل می‌کند: DB، Collector، Valuation، Relative، Strategy A/B، Executor، بله و Scheduler.

### `app/database.py`
ارتباط Python با PostgreSQL و Sessionهای دیتابیس را ایجاد می‌کند.

### `app/models.py`
تعریف 20 جدول PostgreSQL است؛ یعنی مشخص می‌کند چه اطلاعاتی با چه ستون‌هایی ذخیره شوند.

### `app/contracts.py`
شکل استاندارد داده‌هایی را تعیین می‌کند که بین اجزای ربات ردوبدل می‌شوند؛ مثل Market Snapshot، Valuation و Signal.

### `app/config_loader.py`
همه YAMLها را می‌خواند و قبل از اجرا کنترل می‌کند که سیاست‌های مهم اشتباه نشده باشند؛ مثلاً Best Ask only تغییر نکرده باشد.

### `app/units.py`
تبدیل درصد و واحد درصد به Fraction و بالعکس را یکدست می‌کند تا خطای ×100/÷100 پیش نیاید.

### `app/jalali_utils.py`
تبدیل تاریخ شمسی و میلادی و طول ماه شمسی را بدون کتابخانه خارجی انجام می‌دهد.

### `app/collector.py`
تنها بخش مجاز برای تماس با APIهای بازار است. در هر Cycle TGJU، IME و TSETMC را می‌خواند و داده استاندارد می‌سازد. Strategyها حق API Call مستقیم ندارند.

### `app/valuation_engine.py`
حباب‌ها را محاسبه می‌کند:
- طلای خالص ریالی
- Fair Value شمش و سکه
- حباب شمش و سکه
- Intrinsic Bubble هر صندوق با وزن ماهانه
- Nominal Bubble = Best Ask / NAV - 1
- Total Bubble دقیق و ضربی

### `app/relative_value_engine.py`
صندوق‌ها را نسبت به عیار و Baseline تاریخی مقایسه می‌کند، تمام مسیرهای fund→fund را بررسی می‌کند و Net Executable Edge را بعد از Bid/Ask و Fee می‌سازد.

### `app/pipeline.py`
ترتیب اجرای هر چرخه را مدیریت می‌کند. سه حالت دارد:
- WARMUP
- ACTIVE
- CLOSE
و تضمین می‌کند Signal قبل از Execution ثبت/اعلام شود.

### `app/scheduler.py`
تقویم و ساعت اجرای ربات را مدیریت می‌کند و اجازه نمی‌دهد خارج از شنبه تا چهارشنبه و ساعات تعیین‌شده Cycle بازار اجرا شود.

### `app/repository.py`
لایه واسط Pipeline و PostgreSQL است. Snapshotها، Valuationها، Relativeها و Signalها را ذخیره می‌کند و State لازم Strategyها را بعد از Restart از دیتابیس بازسازی می‌کند.

### `app/daily_aggregator.py`
اطلاعات روز را به Daily Summary تبدیل می‌کند. برای هر صندوق میانگین/کمینه/بیشینه/آخرین حباب را می‌سازد و Trade Value/Count آخر روز را نگه می‌دارد. MA7 Strategy B از روزهای کامل گذشته استفاده می‌کند.

### `app/asset_report_monitor.py`
بررسی می‌کند گزارش ماهانه ترکیب دارایی صندوق‌ها به‌روزرسانی شده یا نه؛ در صورت عقب‌افتادگی از طریق بله یادآوری می‌کند.

---

# `app/providers/` — اتصال به منابع بازار

### `app/providers/tgju_adapter.py`
دلار آزاد ریالی و اونس جهانی را از TGJU می‌گیرد و freshness آن‌ها را کنترل می‌کند.

### `app/providers/tsetmc_adapter.py`
برای صندوق‌ها از TSETMC قیمت‌ها، NAV ابطال، Best Bid/Ask و ارزش/تعداد معاملات را می‌گیرد. قیمت Valuation فقط Best Ask است.

### `app/providers/ime_adapter.py`
شمش و سکه را از بازار زنده IME می‌گیرد. قیمت معتبر Valuation برای هر دو فقط Best Ask است.

### `app/providers/api_guard.py`
اگر یک API نتواند داده بدهد، خطا را می‌گیرد، هشدار بله با نام منبع/عملیات می‌سازد و اجازه نمی‌دهد داده نامعتبر ساکت وارد محاسبات شود.

---

# `app/strategies/` — تصمیم‌گیری

### `app/strategies/base.py`
قالب مشترک Strategyها.

### `app/strategies/strategy_a_relative_buy_hold.py`
Signalهای Strategy A را تولید می‌کند. Strategy A همیشه یک پوزیشن دارد و اگر مقصدی حداقل 0.50pp Net Edge بهتر باشد Signal سوییچ می‌دهد.

### `app/strategies/strategy_b_entry_state.py`
State Machine ورودهای Strategy B را نگه می‌دارد: Entry 1، Rearm 1.5pp، Entry 2/3/4 و MA7 fallback فقط برای Entry 2.

### `app/strategies/strategy_b_threshold_10_10.py`
Signalهای Strategy B را با اولویت EXIT → ROTATION → ENTRY → MA7 fallback تولید می‌کند.

---

# `app/execution/` — حساب فرضی و اجرای Signal

### `app/execution/router.py`
Signal هر Strategy را به Executor درست خودش می‌فرستد.

### `app/execution/strategy_a_math.py`
محاسبات عددی خرید اولیه و سوییچ کامل Strategy A را انجام می‌دهد.

### `app/execution/strategy_a_executor.py`
حساب فرضی A را مدیریت می‌کند: خرید اولیه عیار، فروش Best Bid، خرید Best Ask، Fee، P&L، Position lineage و Account Snapshot.

### `app/execution/strategy_b_math.py`
محاسبات ورود، خروج، آفران و Rotation Strategy B.

### `app/execution/strategy_b_executor.py`
حساب فرضی B را اجرا می‌کند: چند Position، سقف 30٪ هر صندوق، سقف 100٪ طلا، بدون Partial Entry، آفران و ثبت Transactionها.

---

# `app/state/`

### `app/state/strategy_b_runtime.py`
State Strategy B را از PostgreSQL می‌سازد؛ از جمله تعداد Entryهای انجام‌شده، وضعیت Rearm و MA7 هفت روز کامل گذشته.

---

# `app/notifications/` — بله

### `app/notifications/bale_client.py`
ارتباط HTTP خام با Bot API بله؛ ارسال متن و فایل.

### `app/notifications/templates.py`
ظاهر پیام‌ها را می‌سازد: آیکون‌ها، تیترها، فرمت Strategy A/B، Signal، Error و پایان روز.

### `app/notifications/service.py`
هماهنگ‌کننده اعلان‌هاست: پیام 12:00، 12:05، Signalها، Error API، 17:00، CSV روزانه، بکاپ چهارشنبه و یادآوری ترکیب دارایی.

---

# `app/reporting/` — گزارش و خروجی فایل

### `app/reporting/account_reporter.py`
آخرین وضعیت حساب فرضی A/B را از DB می‌خواند تا پیام‌های 12:00 و 17:00 ساخته شوند.

### `app/reporting/csv_exporter.py`
فایل CSV تمام Signalهای روز را می‌سازد و چهارشنبه کل دیتابیس را به ZIP شامل CSV جدا برای هر جدول تبدیل می‌کند.

---

# پوشه `scripts/` — ابزارهای مدیریت VPS

### `scripts/install_vps.sh`
نصب اصلی: PostgreSQL Docker، venv، کتابخانه‌ها، ساخت DB، Seed، Preflight و نصب/روشن کردن systemd service.

### `scripts/init_db.py`
20 جدول را می‌سازد و 10 صندوق + آفران و آخرین ترکیب دارایی را Seed می‌کند.

### `scripts/check_db.py`
برای بررسی سریع سلامت اتصال و وجود جداول DB.

### `scripts/preflight.py`
قبل از روشن شدن ربات بررسی می‌کند Secretها، PostgreSQL، 20 جدول، 10 صندوق و ترکیب دارایی موجود باشند.

### `scripts/smoke_live.py`
تست Read-only API زنده است. Signal یا معامله نمی‌سازد؛ فقط Collector و Valuation را با داده واقعی امتحان می‌کند.

### `scripts/migrate_strategy_a_v1.sql`
Migration مربوط به تغییرات تاریخی Strategy A؛ برای DBهای قبلی پروژه مفید است.

### `scripts/migrate_strategy_b_v2.sql`
Migration مربوط به Strategy B.

### `scripts/migrate_schedule_v1.sql`
Migration مربوط به Scheduler/Cycle type. در نصب تازه معمولاً `init_db.py` کافی است، ولی Migrationها برای ارتقای DB قدیمی نگه داشته شده‌اند.

---

# پوشه `deploy/`

### `deploy/systemd/wallex-gold.service.template`
قالب سرویس Linux است. installer آن را با مسیر و کاربر واقعی VPS پر می‌کند تا ربات بعد از Restart سرور خودکار دوباره بالا بیاید.

---

# پوشه `tests/` — تست‌های خودکار

فایل‌های این پوشه برای اجرای روزانه ربات لازم نیستند، ولی **برای اطمینان از سالم بودن پروژه بسیار مهم‌اند** و عمداً داخل پکیج نهایی مانده‌اند.

- `test_api_guard.py` — هشدار خطای API
- `test_asset_report_schedule.py` — منطق یادآوری گزارش ماهانه
- `test_bale_templates.py` — قالب پیام‌های بله
- `test_end_to_end_pipeline.py` — اتصال اجزای اصلی در یک Pipeline
- `test_executor_wiring.py` — اتصال Executorهای A/B
- `test_ime_adapter.py` — IME و Best Ask
- `test_pipeline_phases.py` — Warmup/Active/Close
- `test_relative_value_engine.py` — Relative Value
- `test_scheduler.py` — روزها و ساعت‌ها
- `test_strategy_a.py` — Strategy A
- `test_strategy_b.py` — Strategy B و Rearm/MA7
- `test_strict_market_policy.py` — ممنوع بودن fallback قیمت
- `test_tsetmc_adapter.py` — TSETMC و NAV/Ask
- `test_valuation_engine.py` — فرمول‌های Valuation

---

# پوشه‌هایی که بعداً خودکار ساخته می‌شوند و داخل ZIP نیستند

### `.venv/`
محیط Python روی VPS؛ installer می‌سازد.

### `logs/`
فایل‌های لاگ ربات؛ `main.py` می‌سازد.

### `output/exports/`
CSV روزانه و ZIP بکاپ دیتابیس؛ هنگام نیاز ساخته می‌شود.

### `runtime_state/`
Lock file جلوگیری از اجرای همزمان دو نسخه ربات؛ خودکار ساخته می‌شود.

### `.env`
فایل Secret واقعی؛ عمداً داخل ZIP نیست و فقط روی VPS ساخته می‌شود.
