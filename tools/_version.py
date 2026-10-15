"""Single source of truth for the package version.

The version lives **only** in ``pyproject.toml`` (``[project].version``).
Everything else (config ``_skill.version``, the email footer, tests) reads it
from here, so bumping the version is a one-line edit in ``pyproject.toml``.

Resolution order:
1. Installed distribution metadata (``importlib.metadata``) when the package
   was pip/uv-installed as a normal dist.
2. Fallback: parse ``pyproject.toml`` directly — the common case here, since the
   project usually runs from source as a uv *virtual* workspace (no installed
   dist metadata). A regex avoids needing ``tomllib`` (absent on Python 3.10).
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version as _dist_version
from pathlib import Path

_DIST_NAME = "economic-news-analysis"
_FALLBACK = "0+unknown"


def _version_from_pyproject() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACK
    # Matches the line `version = "x.y.z"` (only [project].version sits at
    # column 0; dependency pins like "pandas>=2.0.0" are not `version = ...`).
    match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else _FALLBACK


def _resolve_version() -> str:
    try:
        return _dist_version(_DIST_NAME)
    except PackageNotFoundError:
        return _version_from_pyproject()


__version__ = _resolve_version()
