"""Load and merge Runtime Skill configuration."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ._version import __version__ as _PKG_VERSION
from .schema_validation import validate_file

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILL_CONFIG = ROOT / "skill.yaml"


def _load_mapping(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the document root")
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _resolve_path(value: str | None) -> str | None:
    if not value:
        return value
    path = Path(str(value))
    if not path.is_absolute():
        path = ROOT / path
    return str(path)


def _parse_env_value(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def load_env_file(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        os.environ.setdefault(key, _parse_env_value(value))


def load_runtime_config(
    config_path: Path | None = None,
    skill_path: Path = DEFAULT_SKILL_CONFIG,
) -> dict[str, Any]:
    skill = _load_mapping(skill_path)
    defaults = deepcopy(skill.get("defaults", {}))
    runtime_config: dict[str, Any] = {}
    if config_path is not None:
        runtime_config = _load_mapping(config_path)

    config = deep_merge(defaults, runtime_config)
    config.setdefault("mode", "briefing")
    config.setdefault("output_language", "zh-CN")
    config.setdefault("output_format", "json")
    config.setdefault("source_config", str(ROOT / "sources" / "rss_sources.yaml"))
    config.setdefault("max_items_per_source", 5)
    config.setdefault("max_ranked_items", 20)
    config.setdefault("daily_time_budget_seconds", 900)
    # Backward compatibility with old single timeout_seconds
    if "timeout_seconds" in config:
        old = int(config["timeout_seconds"])
        config.setdefault("connect_timeout", max(1, old // 3))
        config.setdefault("read_timeout", old)
    else:
        config.setdefault("connect_timeout", 10)
        config.setdefault("read_timeout", 40)
    config.setdefault("fetch_enabled", True)
    config.setdefault("output_dir", None)
    config.setdefault("deliver_email", False)
    config.setdefault("filters", {})
    config.setdefault("focus_assets", [])
    config.setdefault("input_items", [])
    config.setdefault("email", {})
    config.setdefault("env_file", None)
    config.setdefault("market_data", {})
    config["market_data"] = deep_merge(
        {
            "enabled": False,
            "provider": "yfinance",
            "source_config": "sources/market_sources.yaml",
            "watchlist_config": "sources/stock_watchlist.yaml",
            "period": "5d",
            "interval": "1d",
        },
        config["market_data"],
    )
    config["env_file"] = _resolve_path(config.get("env_file"))
    if config["env_file"]:
        load_env_file(Path(str(config["env_file"])))
    # Keep plugin runtime logs beside the deployed skill even when Hermes is
    # launched from another working directory.
    config["log_path"] = _resolve_path(config.get("log_path"))
    config["source_config"] = _resolve_path(str(config["source_config"]))
    config["market_data"]["source_config"] = _resolve_path(
        str(config["market_data"]["source_config"])
    )
    config["market_data"]["watchlist_config"] = _resolve_path(
        config["market_data"].get("watchlist_config")
    )
    validate_file(config, ROOT / "schemas" / "runtime_input.schema.json")
    # Version is sourced from pyproject.toml (single source of truth) via
    # tools/_version.py — NOT from skill.yaml — so a bump is one line in
    # pyproject. ``skill.yaml`` may still carry a name but no longer a version.
    config["_skill"] = {
        "name": skill.get("name", "EconomicNewsAnalysis"),
        "version": _PKG_VERSION,
    }
    return config
