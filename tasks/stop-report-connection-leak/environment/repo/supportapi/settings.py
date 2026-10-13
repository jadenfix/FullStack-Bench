# runtime settings. everything is env-driven so the platform can override per environment.
# SUPPORT_<NAME> wins over DEFAULTS; a few platform names are honoured too (see _ALIASES).
import os

DEFAULTS = {
    "port": "8080",
    "pool_max": "8",
    "pool_wait_s": "2",
    "report_chunk_rows": "20",
    "report_chunk_delay_s": "0.15",   # the mobile client renders a chunk at a time; pacing keeps it smooth
    "default_deadline_ms": "20000",
    "dsn_source": "database,url",
}

_ALIASES = {"port": ["PORT"]}


class _Settings:
    def __getattr__(self, name):
        for env_name in ["SUPPORT_" + name.upper()] + _ALIASES.get(name, []):
            if os.environ.get(env_name):
                return os.environ[env_name]
        if name in DEFAULTS:
            return DEFAULTS[name]
        raise AttributeError(name)

    def dsn(self):
        # the platform injects the connection string under the name built from dsn_source
        var = "_".join(self.dsn_source.split(",")).upper()
        return os.environ[var]


S = _Settings()
