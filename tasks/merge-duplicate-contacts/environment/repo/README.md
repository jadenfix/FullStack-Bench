# contacts

The CRM's contact store. Python stdlib HTTP server + Postgres.

    python -m contacts                       # the API; needs the database connection in the environment
    python -m contacts.importer <csv>        # the partner batch importer (the `contacts-import` job)

## API

- `POST /contacts {"tenant_id", "email", "name"}`: 201 with the new contact, or 200 with the
  existing active contact for that tenant and key.
- `GET /contacts/<id>`: the contact with its activities.
- `POST /contacts/<id>/touch`: records a page view (`last_seen_at`).

## Data

`contacts`, `activities`, `deals`. Migrations in `migrations/`; the latest one is applied with
`psql` by hand. The key rule and the duplicate policy are in `docs/DATA_CONTRACT.md`.
