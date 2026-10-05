-- support refunds go through the payments dashboard; refunded_cents is filled by the nightly sync job
ALTER TABLE orders ADD COLUMN refunded_cents integer NOT NULL DEFAULT 0;
CREATE TABLE order_events (
    id        bigserial PRIMARY KEY,
    order_id  text NOT NULL,
    kind      text NOT NULL,
    detail    text,
    at        timestamptz NOT NULL DEFAULT now()
);
