BEGIN;

ALTER TABLE positions_current
    ADD COLUMN IF NOT EXISTS parent_position_id BIGINT NULL;

COMMIT;
