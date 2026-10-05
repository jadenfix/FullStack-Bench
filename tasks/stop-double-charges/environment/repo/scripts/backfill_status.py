# one-off from the 2025 migration: set status from the old `paid` boolean. already applied; kept for history.
import os

import psycopg

with psycopg.connect(os.environ["DATABASE_URL"]) as c:
    c.execute("UPDATE orders SET status = CASE WHEN paid THEN 'paid' ELSE 'pending' END WHERE status IS NULL")
