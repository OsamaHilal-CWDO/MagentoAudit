"""Utility helpers for parsing CLI and DB output."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


def normalize_header_key(value: str) -> str:
    """Normalize table header labels into stable snake_case keys."""
    clean = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return clean.strip("_")


def parse_magento_table(output: str) -> List[Dict[str, str]]:
    """Parse Magento CLI ASCII table output into rows."""
    rows: List[Dict[str, str]] = []
    headers: Optional[List[str]] = None

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        columns = [col.strip() for col in stripped.strip("|").split("|")]
        if headers is None:
            headers = [normalize_header_key(col) for col in columns]
            continue
        if len(columns) != len(headers):
            continue
        rows.append({headers[i]: columns[i] for i in range(len(headers))})

    return rows


def parse_module_names(output: str) -> List[str]:
    """Extract module names from `bin/magento module:status` output."""
    modules: List[str] = []
    for line in output.splitlines():
        candidate = line.strip().strip("-").strip()
        if re.fullmatch(r"[A-Za-z0-9_]+", candidate):
            modules.append(candidate)
    return sorted(set(modules))


def parse_key_value_lines(output: str, separator: str = "\t") -> Dict[str, str]:
    """Parse simple key/value output lines."""
    data: Dict[str, str] = {}
    for line in output.splitlines():
        parts = [p.strip() for p in line.split(separator) if p.strip()]
        if len(parts) >= 2:
            data[parts[0]] = parts[1]
    return data


def parse_bool_status(value: str) -> Optional[bool]:
    """Parse common textual status values into bool."""
    lowered = (value or "").strip().lower()
    if lowered in {"1", "true", "enabled", "on", "yes"}:
        return True
    if lowered in {"0", "false", "disabled", "off", "no"}:
        return False
    return None


def detect_cache_backend(cache_config: Dict) -> Tuple[str, Dict]:
    """Detect cache backend type and expose safe backend options."""
    backend = str(cache_config.get("backend") or "").lower()
    options = cache_config.get("backend_options") or {}

    normalized_options = {}
    for key in ("server", "port", "database", "db", "compress_data", "prefix"):
        if key in options:
            normalized_options[key] = options[key]

    if "redis" in backend or "redis" in str(options).lower():
        return "redis", normalized_options
    if "mysql" in backend or "db" in backend or "database" in backend:
        return "database", normalized_options
    if "file" in backend:
        return "filesystem", normalized_options
    if not backend:
        return "filesystem", normalized_options
    return backend, normalized_options


def safe_float(value: str) -> Optional[float]:
    """Convert a string to float safely."""
    try:
        return float(value)
    except Exception:
        return None


def safe_int(value: str) -> Optional[int]:
    """Convert a string to int safely."""
    try:
        return int(value)
    except Exception:
        return None
