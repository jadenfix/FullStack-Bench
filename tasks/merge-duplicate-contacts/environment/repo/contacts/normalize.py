"""The contact key. See docs/DATA_CONTRACT.md: a contact is identified within a tenant by its
normalized email: whitespace trimmed, lowercased, and a `+tag` suffix on the local part removed."""


def email_key(email: str) -> str:
    e = (email or "").strip().lower()
    if "@" not in e:
        return e
    local, domain = e.rsplit("@", 1)
    return f"{local.split('+', 1)[0]}@{domain}"
