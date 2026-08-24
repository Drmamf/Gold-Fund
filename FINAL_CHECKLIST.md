# Final Production Checklist

## Code status

- [x] PostgreSQL 20-table schema
- [x] Instrument/config seeding
- [x] TGJU adapter
- [x] TSETMC adapter
- [x] IME adapter
- [x] Shared Collector
- [x] Strict Best-Ask valuation policy
- [x] Shared Valuation Engine
- [x] Shared Relative Value Engine
- [x] Strategy A + paper executor
- [x] Strategy B + MA7/rearm + paper executor + Afran
- [x] PostgreSQL Repository / restart state
- [x] Daily Aggregator / MA7 source data
- [x] Signal != Execution separation
- [x] Bale signal/status/API-error notifications
- [x] Daily Signals CSV
- [x] Wednesday 18:00 full DB ZIP backup
- [x] Monthly asset-composition reminders
- [x] Scheduler: Sat-Wed 12:00 / 12:03 / 12:05-16:59 / 17:00
- [x] Single-instance VPS lock
- [x] BotRun heartbeat
- [x] systemd installer
- [x] preflight script
- [x] read-only live API smoke test
- [x] 44 automated tests passing

## VPS-only values to fill

- [ ] POSTGRES_PASSWORD
- [ ] DATABASE_URL with the same password
- [ ] BALE_BOT_TOKEN
- [ ] BALE_CHAT_ID

Then run:

```bash
./scripts/install_vps.sh
```

During market hours, optional but strongly recommended:

```bash
source .venv/bin/activate
python scripts/smoke_live.py
```
