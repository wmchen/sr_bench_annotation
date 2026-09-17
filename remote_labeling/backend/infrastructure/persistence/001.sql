CREATE TABLE IF NOT EXISTS datasets (
 id TEXT PRIMARY KEY, attribute TEXT NOT NULL, root TEXT NOT NULL,
 import_version INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, errors TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
 dataset TEXT NOT NULL REFERENCES datasets(id), id TEXT NOT NULL, position INTEGER NOT NULL,
 images TEXT NOT NULL, dimensions TEXT NOT NULL, image_version TEXT NOT NULL,
 draft TEXT NOT NULL, formal TEXT, revision INTEGER NOT NULL DEFAULT 0,
 committed_revision INTEGER, modified INTEGER NOT NULL DEFAULT 0,
 complete INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL,
 PRIMARY KEY(dataset,id)
);
CREATE INDEX IF NOT EXISTS sample_order ON samples(dataset,position);
CREATE TABLE IF NOT EXISTS shares (
 id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, role TEXT NOT NULL,
 dataset TEXT, sample TEXT, expires REAL, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, share TEXT NOT NULL REFERENCES shares(id),
 nickname TEXT NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS leases (
 dataset TEXT NOT NULL, sample TEXT NOT NULL, id TEXT NOT NULL,
 generation TEXT NOT NULL, session TEXT NOT NULL, tab TEXT NOT NULL, expires REAL NOT NULL,
 PRIMARY KEY(dataset,sample), FOREIGN KEY(dataset,sample) REFERENCES samples(dataset,id)
);
CREATE TABLE IF NOT EXISTS operations (
 session TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(session,id)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, session TEXT NOT NULL, tab TEXT NOT NULL,
 dataset TEXT NOT NULL, sample TEXT NOT NULL, state TEXT NOT NULL,
 request TEXT NOT NULL, result TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state,created);
CREATE TABLE IF NOT EXISTS exports (
 id TEXT PRIMARY KEY, dataset TEXT NOT NULL, state TEXT NOT NULL,
 manifest TEXT NOT NULL, path TEXT, error TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT, sample TEXT,
 kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL
);
PRAGMA user_version=1;
