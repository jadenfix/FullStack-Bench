# Contacts data contract

## Identity

Within a tenant, a contact is identified by its **key**: the email address with surrounding
whitespace removed, lower-cased, and any `+tag` suffix on the local part removed
(`" Priya.Raman+news@Acme-Example.com "` and `priya.raman@acme-example.com` are one contact).
`contacts.email` keeps the address as entered; `contacts.email_norm` holds the key.

At most one **active** contact may exist per tenant and key.

## Duplicate policy

When several active contacts share a key, the **survivor** is the earliest created (ties broken
by the smaller `id`). Every other member of the group is a **duplicate**:

- its `status` becomes `merged` and `merged_into` names the survivor;
- its activities and deals are re-pointed to the survivor;
- nothing is deleted, and the survivor's own fields are not changed.

Contacts, activities and deals are customer history: rows are never deleted.
