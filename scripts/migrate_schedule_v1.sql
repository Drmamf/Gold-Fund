BEGIN;

ALTER TABLE market_cycles
    ADD COLUMN IF NOT EXISTS cycle_type VARCHAR(32) NOT NULL DEFAULT 'ACTIVE';

ALTER TABLE market_cycles
    ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_market_cycles_scheduled_for
    ON market_cycles (scheduled_for);

CREATE UNIQUE INDEX IF NOT EXISTS uq_market_cycle_schedule_slot
    ON market_cycles (market_date, cycle_type, scheduled_for);

COMMIT;
