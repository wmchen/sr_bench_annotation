BEGIN IMMEDIATE;
CREATE TABLE owner_ip_bindings (
 ip TEXT PRIMARY KEY, owner_digest TEXT NOT NULL,
 nickname TEXT NOT NULL, bound_at REAL NOT NULL
);
ALTER TABLE sessions ADD COLUMN owner_ip TEXT;
DELETE FROM leases WHERE session IN (
 SELECT s.id FROM sessions s JOIN shares h ON h.id=s.share
 WHERE h.role='owner'
);
DELETE FROM sessions WHERE share IN (SELECT id FROM shares WHERE role='owner');
PRAGMA user_version=2;
COMMIT;
