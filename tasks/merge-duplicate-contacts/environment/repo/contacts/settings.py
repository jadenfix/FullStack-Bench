# runtime settings. everything is env-driven so the platform can override per environment.
# CONTACTS_<NAME> wins over DEFAULTS; a few platform names are honoured too (see _ALIASES).
import os

DEFAULTS = {
    "port": "8080",
    "tables": "contacts=contacts,activities=activities,deals=deals",
    "import_batch": "500",
    "dsn_source": "database,url",
}

_ALIASES = {"port": ["PORT"]}


class _Settings:
    def __getattr__(self, name):
        for env_name in ["CONTACTS_" + name.upper()] + _ALIASES.get(name, []):
            if os.environ.get(env_name):
                return os.environ[env_name]
        if name in DEFAULTS:
            return DEFAULTS[name]
        raise AttributeError(name)

    def table(self, logical):
        mapping = dict(kv.split("=", 1) for kv in self.tables.split(",") if "=" in kv)
        return mapping.get(logical, logical)

    def dsn(self):
        # the platform injects the connection string under the name built from dsn_source
        var = "_".join(self.dsn_source.split(",")).upper()
        return os.environ[var]


S = _Settings()
