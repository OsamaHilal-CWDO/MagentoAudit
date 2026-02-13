"""Cache configuration and runtime cache-status checks."""

from __future__ import annotations

from typing import Dict

from ..base import Colors, MagentoEnvironment
from ..utils import detect_cache_backend, parse_bool_status, parse_magento_table


class CacheAuditModule:
    """Audit Magento cache type status and backend configuration."""

    name = "cache"

    def __init__(self, env: MagentoEnvironment):
        self.env = env

    def _parse_cache_status(self, output: str) -> Dict[str, bool]:
        rows = parse_magento_table(output)
        if rows:
            parsed = {}
            for row in rows:
                values = list(row.values())
                if len(values) < 2:
                    continue
                cache_type = values[0].strip()
                status_value = values[1].strip()
                bool_status = parse_bool_status(status_value)
                if bool_status is None:
                    bool_status = "enabled" in status_value.lower()
                parsed[cache_type] = bool_status
            return parsed

        # Fallback parser for `cache_type: 1` output.
        fallback = {}
        for line in output.splitlines():
            if ":" not in line:
                continue
            left, right = line.split(":", 1)
            cache_type = left.strip()
            bool_status = parse_bool_status(right.strip())
            if bool_status is not None:
                fallback[cache_type] = bool_status
        return fallback

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Reviewing cache configuration and cache status...{Colors.RESET}")

        result = {
            "status": "good",
            "cache_types": {},
            "disabled_cache_types": [],
            "cache_backend": {},
            "full_page_cache_application": None,
            "full_page_cache_ttl": None,
        }

        cache_status_result = self.env.run_magento("cache:status", timeout=90)
        if cache_status_result.ok:
            cache_types = self._parse_cache_status(cache_status_result.stdout)
            disabled = sorted([k for k, enabled in cache_types.items() if not enabled])
            result["cache_types"] = cache_types
            result["disabled_cache_types"] = disabled
        else:
            result["cache_types_error"] = cache_status_result.stderr or "cache:status failed"

        # Config-level cache backend details from app/etc/env.php
        env_config = self.env.load_env_config()
        frontend = env_config.get("cache", {}).get("frontend", {})
        default_frontend = frontend.get("default", {}) if isinstance(frontend, dict) else {}
        page_frontend = frontend.get("page_cache", {}) if isinstance(frontend, dict) else {}

        default_backend, default_options = detect_cache_backend(default_frontend)
        page_backend, page_options = detect_cache_backend(page_frontend)

        result["cache_backend"] = {
            "default": {"type": default_backend, "options": default_options},
            "page_cache": {"type": page_backend, "options": page_options},
        }

        # Magento config settings for full page cache.
        app_result = self.env.run_magento("config:show system/full_page_cache/caching_application")
        if app_result.ok and app_result.stdout:
            raw = app_result.stdout.strip()
            mapping = {"1": "built_in", "2": "varnish"}
            result["full_page_cache_application"] = mapping.get(raw, raw)

        ttl_result = self.env.run_magento("config:show system/full_page_cache/ttl")
        if ttl_result.ok and ttl_result.stdout:
            result["full_page_cache_ttl"] = ttl_result.stdout.strip()

        # Status evaluation
        critical_cache_types = {"config", "layout", "block_html", "full_page"}
        disabled_critical = [
            cache_type
            for cache_type in result["disabled_cache_types"]
            if cache_type in critical_cache_types
        ]
        result["disabled_critical_cache_types"] = disabled_critical

        if disabled_critical:
            result["status"] = "critical"
        elif result["disabled_cache_types"]:
            result["status"] = "warning"
        elif default_backend == "filesystem":
            result["status"] = "warning"

        status_color = (
            Colors.GREEN
            if result["status"] == "good"
            else Colors.ORANGE
            if result["status"] == "warning"
            else Colors.RED
        )

        print(
            f"{status_color}Default cache backend: {default_backend} | "
            f"Page cache backend: {page_backend} | "
            f"Disabled cache types: {len(result['disabled_cache_types'])}{Colors.RESET}"
        )

        return result
