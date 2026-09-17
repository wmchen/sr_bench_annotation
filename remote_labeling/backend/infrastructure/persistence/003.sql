-- A prepared operation owns its dataset until finalized or rolled back.
BEGIN IMMEDIATE;
CREATE TABLE writebacks (
 session TEXT NOT NULL, operation TEXT NOT NULL, dataset TEXT NOT NULL,
 sample TEXT NOT NULL, signature TEXT NOT NULL, files TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','done','rolled_back')),
 error TEXT, created REAL NOT NULL,
 PRIMARY KEY(session,operation)
);
CREATE UNIQUE INDEX active_writeback ON writebacks(dataset) WHERE state='prepared';
PRAGMA user_version=3;
COMMIT;
