-- Strategy A schema additions.
-- Safe to run on an existing PostgreSQL database.

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS source_units NUMERIC(24,8),
    ADD COLUMN IF NOT EXISTS target_units NUMERIC(24,8),
    ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Added by the shared Relative Value engine in the previous stage.
ALTER TABLE relative_value_snapshot
    ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb;
