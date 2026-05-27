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

    # Local directory for job assets (uploaded firmware, artifacts).  Defaults to
    # a jobs/ subdirectory next to the DB.
    jobs_dir: str = ""

    # WipperSnapper Arduino + protoMQ defaults for the Arduino WS test job form.
    # Override via HIL_WIPPERSNAPPER_ARDUINO_REPO / HIL_PROTOMQ_REPO / HIL_PROTOMQ_DEFAULT_REF.
    wippersnapper_arduino_repo: str = "https://github.com/adafruit/Adafruit_WipperSnapper_Arduino.git"
    protomq_repo: str = "https://github.com/tyeth/protomq.git"
    protomq_default_ref: str = "main"

    # PlatformIO defaults for the Arduino WS test job form.
    # Override via HIL_PIO_DEFAULT_ENV / HIL_SERIAL_DEFAULT_PORT.
    pio_default_env: str = "adafruit_feather_esp32s3"
    serial_default_port: str = "/dev/ttyACM0"

    # Default MQTT broker host for the Arduino WS test job form.
    # Override via HIL_MQTT_DEFAULT_HOST.
    mqtt_default_host: str = "127.0.0.1"

    # LAN IP the DUT uses to reach the controller when protomq/build run on the
    # controller (per-phase execution-location). Override via HIL_CONTROLLER_IP.
    controller_ip: str = "192.168.1.169"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def resolve_jobs_dir() -> str:
    """Local directory for job assets (uploaded firmware, captured logs).

    ``HIL_JOBS_DIR`` when set, else a ``jobs/`` subdirectory next to the DB.
    Shared by the web router and the queue worker so both agree on the path.
    """
    cfg = get_settings()
    if cfg.jobs_dir:
        return cfg.jobs_dir
    db = cfg.db_path
    return str(Path(db).parent / "jobs") if db else "/tmp/hil-jobs"
