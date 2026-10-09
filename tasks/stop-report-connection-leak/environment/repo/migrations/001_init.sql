CREATE TABLE customers (
    id            text PRIMARY KEY,
    name          text NOT NULL,
    plan          text NOT NULL,
    open_tickets  integer NOT NULL DEFAULT 0
);
CREATE TABLE tickets (
    id            text PRIMARY KEY,
    customer_id   text NOT NULL REFERENCES customers (id),
    subject       text NOT NULL,
    opened_at     timestamptz NOT NULL
);
CREATE INDEX tickets_customer ON tickets (customer_id);
CREATE TABLE ticket_events (
    id         bigserial PRIMARY KEY,
    ticket_id  text NOT NULL REFERENCES tickets (id),
    kind       text NOT NULL,
    note       text,
    at         timestamptz NOT NULL
);
CREATE INDEX ticket_events_ticket_at ON ticket_events (ticket_id, at);
