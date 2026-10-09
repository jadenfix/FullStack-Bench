-- One active contact per tenant and key. Build it concurrently so production keeps writing;
-- run this file with psql outside a transaction (CREATE INDEX CONCURRENTLY cannot run inside one).
-- Interrupted on 2026-09-30 after it reported a duplicate; see docs/DATA_CONTRACT.md before retrying.
CREATE UNIQUE INDEX CONCURRENTLY contacts_active_key ON contacts (tenant_id, email_norm) WHERE status = 'active';
