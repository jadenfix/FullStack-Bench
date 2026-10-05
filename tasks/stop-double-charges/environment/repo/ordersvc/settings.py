# runtime settings. everything is env-driven so the platform can override per environment.
# ORDERS_<NAME> wins over DEFAULTS; a few legacy names are still honoured (see _ALIASES).
import os

DEFAULTS = {
    "port": "8080",
    "currency": "usd",
    "charge_timeout_s": "3",
    "tables": "orders=orders,audit=order_events",
    "pay_provider": "tillpoint",
    "log_bodies": "0",
}

_ALIASES = {"tables": ["ORDERS_TABLE_MAP"], "port": ["PORT"]}


class _Settings:
    def __getattr__(self, name):
        for env_name in ["ORDERS_" + name.upper()] + _ALIASES.get(name, []):
            if os.environ.get(env_name):
                return os.environ[env_name]
        if name in DEFAULTS:
            return DEFAULTS[name]
        raise AttributeError(name)

    def table(self, logical):
        mapping = dict(kv.split("=", 1) for kv in self.tables.split(",") if "=" in kv)
        return mapping.get(logical, logical)

    def secret_env(self, *parts):
        # e.g. secret_env("api", "key") -> <PROVIDER>_API_KEY for the configured provider
        return "_".join([self.pay_provider] + list(parts)).upper()


S = _Settings()
