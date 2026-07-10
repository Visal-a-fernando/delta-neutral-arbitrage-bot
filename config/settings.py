"""config/settings.py — Load and validate configuration."""

import os
import yaml
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def load_config(path: str = "config/config.yaml") -> dict:
    """Load YAML config and resolve environment variable references."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    logger.info(f"Config loaded from {path}")
    return cfg


def get_env(key_name: str, required: bool = True) -> Optional[str]:
    """Resolve an environment variable by name. Raises if required and missing."""
    val = os.environ.get(key_name)
    if val is None and required:
        raise EnvironmentError(
            f"Required environment variable '{key_name}' is not set. "
            f"Add it to your .env file or shell environment."
        )
    return val


def get_exchange_credentials(exchange_cfg: dict) -> tuple[str, str]:
    """Return (api_key, api_secret) for an exchange config block."""
    key    = get_env(exchange_cfg["api_key_env"])
    secret = get_env(exchange_cfg["api_secret_env"])
    return key, secret
