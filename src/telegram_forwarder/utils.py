import os
import re
import logging
from dotenv import load_dotenv
from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

# Regex to match ${VAR_NAME}
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def get_conf(config: dict, key: str):
    """Get a top‑level value from the loaded YAML configuration."""
    value = config.get(key)
    if value is None:
        raise ValueError(f"Config key '{key}' not found in config.yml")
    return value


def _resolve_env_vars(obj):
    """Recursively replace ${VAR} with environment variable values."""
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_resolve_env_vars(item) for item in obj]
    elif isinstance(obj, str):
        # Find all matches
        def repl(match):
            var_name = match.group(1)
            value = os.getenv(var_name)
            if value is None:
                raise ValueError(f"Environment variable '{var_name}' not set")
            return value

        return ENV_VAR_PATTERN.sub(repl, obj)
    else:
        return obj


def load_yaml_config(path: str = "config.yml") -> dict:
    """Load and return the YAML configuration."""
    load_dotenv(override=True)

    yaml = YAML()
    with open(path, "r") as file:
        config = yaml.load(file)
    return _resolve_env_vars(config)


def normalize_identifier(identifier):
    """
    Convert a user‑supplied identifier to a form we can match against.
    Returns a tuple (type, value) where type is 'id' or 'username',
    or None if the identifier cannot be parsed.
    """
    if isinstance(identifier, int):
        return ("id", identifier)

    # Try converting string to integer (numeric ID)
    try:
        return ("id", int(identifier))
    except ValueError:
        pass

    s = identifier.strip()

    # Handle URLs: remove scheme and domain
    match = re.match(r"^(https?://)?t\.me/", s)
    if match:
        s = s[match.end() :]
        if s.startswith("/"):
            s = s[1:]

    # Remove leading '@' if present
    if s.startswith("@"):
        s = s[1:]

    # If after all stripping, it's empty, treat as invalid
    if not s:
        return None

    return ("username", s.lower())


def build_outputs_by_name(config: dict) -> dict:
    """
    Extract outputs from config and build a dictionary mapping output name
    to a dict containing webhook_url and embed_color.
    """
    outputs = config.get("outputs", {})
    outputs_by_name = {}
    for name, out_cfg in outputs.items():
        if not out_cfg.get("webhook_url"):
            logger.warning(f"Output '{name}' missing webhook_url, skipping.")
            continue
        outputs_by_name[name] = {
            "webhook_url": out_cfg["webhook_url"],
            "embed_color": out_cfg["embed_color"],
        }
    return outputs_by_name
