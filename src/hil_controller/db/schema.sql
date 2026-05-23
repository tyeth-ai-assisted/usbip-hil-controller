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
    id               TEXT PRIMARY KEY,
    label            TEXT NOT NULL,
    repo             TEXT NOT NULL DEFAULT '',
    pool             TEXT NOT NULL DEFAULT 'public',
    hash             TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    revoked_at       TEXT,
    last_used_at     TEXT,
    allowed_pools    TEXT NOT NULL DEFAULT '[]',
    allowed_profiles TEXT NOT NULL DEFAULT '[]',
    default_profile  TEXT NOT NULL DEFAULT 'bench-protomq',
    capabilities     TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS hosts (
    id                  TEXT PRIMARY KEY,
    role                TEXT NOT NULL DEFAULT '',
    addr                TEXT NOT NULL DEFAULT '',
    transport           TEXT NOT NULL DEFAULT 'ssh',
    ssh_user            TEXT NOT NULL DEFAULT 'pi',
    ssh_key_path        TEXT,
    max_concurrent_jobs INTEGER,
    capabilities_json   TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'available',
    last_seen_at        TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    id                  TEXT PRIMARY KEY,
    host_id             TEXT NOT NULL,
    kind                TEXT NOT NULL,
    model               TEXT NOT NULL DEFAULT '',
    capabilities_json   TEXT NOT NULL DEFAULT '[]',
    usb_json            TEXT,
    pool                TEXT NOT NULL DEFAULT 'public',
    status              TEXT NOT NULL DEFAULT 'available',
    serial_port         TEXT,
    flasher             TEXT
);

CREATE TABLE IF NOT EXISTS auxes (
    id                  TEXT PRIMARY KEY,
    kind                TEXT NOT NULL DEFAULT '',
    model               TEXT NOT NULL DEFAULT '',
    capabilities_json   TEXT NOT NULL DEFAULT '[]',
    interface           TEXT NOT NULL DEFAULT '',
    observability       TEXT NOT NULL DEFAULT 'none',
    pool                TEXT NOT NULL DEFAULT 'public',
    status              TEXT NOT NULL DEFAULT 'available'
);

CREATE TABLE IF NOT EXISTS connections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    aux_id      TEXT NOT NULL,
    device_id   TEXT,
    mux_id      TEXT,
    mux_channel TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    path        TEXT NOT NULL DEFAULT '',
    url         TEXT,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    kind        TEXT NOT NULL DEFAULT 'firmware',  -- firmware | log | artifact
    job_id      TEXT,
    created_at  TEXT NOT NULL,
    purge_at    TEXT,
    purged_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_assets_job ON assets(job_id);
CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    event       TEXT NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    repo        TEXT NOT NULL DEFAULT '',
    entity_id   TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);
