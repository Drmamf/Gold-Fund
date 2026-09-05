# Live Strategy A (Karamad) — parallel to paper

Paper Strategy A/B keep running in `wallex-gold.service`. This worker is a
**separate** Linux Chrome session on Karamad and only consumes Strategy A
signals (`RELATIVE_BUY_HOLD`).

## Money

- Cap on **new** cash: 50,000,000 toman (`500,000,000` rial)
- Budget = `min(قدرت خرید سهام, cap)`
- Rotations invest the live position, including profit
- Symbols: the 10 gold funds. Afran / Strategy B are never sent

## Enable on the VPS

1. Keep `wallex-gold.service` running.
2. Put broker credentials in `.env` (gitignored):

```
KARAMAD_LIVE_ENABLED=true
KARAMAD_DRY_RUN=true
KARAMAD_USERNAME=...
KARAMAD_PASSWORD=...
KARAMAD_MAX_TOMAN=50000000
```

3. `sudo bash scripts/install_live_a.sh`
4. `sudo systemctl start karamad-live-a`

With `KARAMAD_DRY_RUN=true` the worker logs in, fills the form, and **does not
click**. Set `false` only after a dry-run login looks right.

Kill switch: create `runtime_state/LIVE_A_KILL` to skip all live orders without
touching paper.

## Tables

`live_orders` and `live_account_state` — not `transactions` / `positions_current`.
If a sell fills and the buy fails, live state is frozen and Bale is notified.
