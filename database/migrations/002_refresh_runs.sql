-- Refresh history: one row per run, one row per detected change.
-- Lets operations see when a source changed, what changed, and whether
-- the run was published or gated.
CREATE TABLE IF NOT EXISTS refresh_runs (
    run_id      text PRIMARY KEY,                     -- UTC timestamp, e.g. 20261125T180000Z
    status      text NOT NULL CHECK (status IN ('published','published_with_warnings','gated')),
    snapshots   jsonb NOT NULL,                       -- snapshot file per source checked
    n_error     int NOT NULL,
    n_warn      int NOT NULL,
    n_info      int NOT NULL,
    finished_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS change_events (
    event_id    bigserial PRIMARY KEY,
    run_id      text NOT NULL REFERENCES refresh_runs,
    source_id   text NOT NULL,
    kind        text NOT NULL,                        -- store_removed, record_count_drop, ...
    severity    text NOT NULL CHECK (severity IN ('info','warn','error')),
    record_key  text,                                 -- e.g. OSM id; NULL for source-level changes
    detail      jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS change_events_open ON change_events (severity, kind)
    WHERE severity <> 'info';

-- What needs a human: warnings and errors from the most recent run.
CREATE OR REPLACE VIEW open_change_events AS
SELECT e.*, r.status AS run_status, r.finished_at
FROM change_events e
JOIN refresh_runs r USING (run_id)
WHERE e.severity <> 'info'
  AND r.run_id = (SELECT max(run_id) FROM refresh_runs);
