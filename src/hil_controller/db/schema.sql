PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    submitted_by    TEXT NOT NULL DEFAULT '',
    repo            TEXT NOT NULL DEFAULT '',
    request_json    TEXT NOT NULL,
    secrets_profile TEXT NOT NULL DEFAULT 'bench-protomq',
    exclusive_host  INTEGER NOT NULL DEFAULT 0,
    state           TEXT NOT NULL DEFAULT 'queued',
    assigned_host   TEXT,
    assigned_device TEXT,
    result          TEXT,
    summary         TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    seq         INTEGER NOT NULL,
    at          TEXT NOT NULL,
    kind        TEXT NOT NULL,  -- state | log | metric | artifact
    payload_json TEXT NOT NULL,
    UNIQUE(job_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_events_job_seq ON events(job_id, seq);

CREATE TABLE IF NOT EXISTS tokens (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    repo        TEXT NOT NULL DEFAULT '',
    pool        TEXT NOT NULL DEFAULT 'public',
    hash        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    revoked_at  TEXT,
    last_used_at TEXT
);
