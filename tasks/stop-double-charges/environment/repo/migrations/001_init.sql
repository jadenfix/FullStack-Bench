CREATE TABLE orders (
    id            text PRIMARY KEY,
    cart_id       text NOT NULL,
    customer_id   text NOT NULL,
    amount_cents  integer NOT NULL CHECK (amount_cents > 0),
    currency      text NOT NULL DEFAULT 'usd',
    status        text NOT NULL,
    charge_id     text,
    request_id    text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX orders_customer ON orders (customer_id);
