CREATE TABLE contacts (
    id            text PRIMARY KEY,
    tenant_id     text NOT NULL,
    email         text NOT NULL,
    email_norm    text NOT NULL,
    name          text NOT NULL DEFAULT '',
    status        text NOT NULL DEFAULT 'active',   -- active | merged
    merged_into   text REFERENCES contacts (id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz
);
CREATE INDEX contacts_tenant ON contacts (tenant_id, email_norm);
CREATE TABLE activities (
    id          bigserial PRIMARY KEY,
    contact_id  text NOT NULL REFERENCES contacts (id),
    kind        text NOT NULL,
    note        text,
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX activities_contact ON activities (contact_id);
CREATE TABLE deals (
    id            text PRIMARY KEY,
    contact_id    text NOT NULL REFERENCES contacts (id),
    amount_cents  integer NOT NULL,
    stage         text NOT NULL
);
CREATE INDEX deals_contact ON deals (contact_id);
