from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HIL_", env_file=".env", extra="ignore")

    db_path: str = "/var/lib/hil/jobs.db"
    topology_file: str = "/etc/hil/topology.yaml"
    host: str = "0.0.0.0"
    port: int = 8080

    # Bootstrap auth: comma-separated plaintext tokens for initial setup.
    # In production, use scripts/mint-token.py to write argon2-hashed rows instead.
    static_token: str = ""

    long_poll_max_timeout: int = 600
    long_poll_default_timeout: int = 300

    upnp_enabled: bool = False
    upnp_lease_seconds: int = 3600

    # Path to vendor/protomq/scripts/ for the web scripts browser.  Empty = disabled.
    scripts_dir: str = ""


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
