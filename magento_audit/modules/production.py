"""Production readiness checks for Magento deployments."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from urllib.request import Request, urlopen

from ..base import Colors, MagentoEnvironment
from ..utils import safe_int


class ProductionReadinessModule:
    """Validate production mode, cron health, queues, and search backend reachability."""

    name = "production"

    def __init__(self, env: MagentoEnvironment, runtime_options: Optional[Dict] = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def _table_exists(self, table: str) -> bool:
        ok, lines, _ = self.env.run_mysql_query(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE table_schema=DATABASE() AND table_name='{table}';"
        )
        return bool(ok and lines and (safe_int(lines[0]) or 0) == 1)

    def _config_show(self, path: str) -> str:
        result = self.env.run_magento(f"config:show {path}", timeout=20)
        if result.ok and result.stdout:
            return result.stdout.strip()
        return ""

    def _simple_http_check(self, url: str) -> Dict:
        try:
            req = Request(url, headers={"User-Agent": "MagentoHealthAudit/1.0"})
            with urlopen(req, timeout=5) as resp:
                return {"ok": True, "status_code": getattr(resp, "status", 200)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _latest_mtime(self, path: str) -> Optional[float]:
        if not os.path.exists(path):
            return None
        latest = os.path.getmtime(path)
        if os.path.isfile(path):
            return latest
        for current_root, _, files in os.walk(path):
            for file_name in files:
                file_path = os.path.join(current_root, file_name)
                try:
                    latest = max(latest, os.path.getmtime(file_path))
                except Exception:
                    continue
        return latest

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Checking production readiness and operational health...{Colors.RESET}")
        prefix = self.env.get_table_prefix()
        result = {"status": "good"}

        # Deploy mode
        mode_result = self.env.run_magento("deploy:mode:show", timeout=60)
        mode = "unknown"
        if mode_result.ok:
            mode_match = re.search(
                r"Current application mode:\s*([a-zA-Z_]+)",
                mode_result.stdout,
                re.IGNORECASE,
            )
            if mode_match:
                mode = mode_match.group(1).strip().lower()
            for line in mode_result.stdout.splitlines():
                if "Current application mode" in line:
                    mode_part = line.split("Current application mode:", 1)[-1].strip()
                    mode_word = mode_part.split()[0].strip().strip(".")
                    if mode_word:
                        mode = mode_word.lower()
                    break
        result["deploy_mode"] = mode

        # Compilation freshness heuristic
        generated_code = os.path.join(self.env.root_path, "generated", "code")
        app_code = os.path.join(self.env.root_path, "app", "code")
        generated_mtime = self._latest_mtime(generated_code)
        app_code_mtime = self._latest_mtime(app_code)
        stale_compile = bool(
            generated_mtime and app_code_mtime and app_code_mtime > generated_mtime
        )
        result["compilation"] = {
            "generated_code_exists": os.path.isdir(generated_code),
            "generated_latest_mtime": generated_mtime,
            "app_code_latest_mtime": app_code_mtime,
            "potentially_stale": stale_compile,
        }

        # Static content state
        pub_static = os.path.join(self.env.root_path, "pub", "static")
        static_exists = os.path.isdir(pub_static)
        static_files = 0
        if static_exists:
            for _, _, files in os.walk(pub_static):
                static_files += len(files)
                if static_files > 50000:
                    break
        result["static_content"] = {
            "pub_static_exists": static_exists,
            "sampled_file_count": static_files,
        }

        # Cron health
        cron_table = f"{prefix}cron_schedule"
        cron = {
            "table_exists": self._table_exists(cron_table),
            "last_hour_success": None,
            "last_24h_missed": None,
            "last_24h_error": None,
        }
        if cron["table_exists"]:
            now = datetime.now(timezone.utc)
            hour_ago = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            day_ago = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            queries = {
                "last_hour_success": (
                    f"SELECT COUNT(*) FROM {cron_table} "
                    f"WHERE status='success' AND executed_at >= '{hour_ago}';"
                ),
                "last_24h_missed": (
                    f"SELECT COUNT(*) FROM {cron_table} "
                    f"WHERE status='missed' AND scheduled_at >= '{day_ago}';"
                ),
                "last_24h_error": (
                    f"SELECT COUNT(*) FROM {cron_table} "
                    f"WHERE status='error' AND scheduled_at >= '{day_ago}';"
                ),
            }
            for key, sql in queries.items():
                ok, lines, _ = self.env.run_mysql_query(sql)
                if ok and lines:
                    cron[key] = safe_int(lines[0])
        result["cron"] = cron

        # Queue consumer table snapshots (if message queue is used)
        queue_table = f"{prefix}queue_message_status"
        queues = {"table_exists": self._table_exists(queue_table), "status_counts": {}}
        if queues["table_exists"]:
            ok, lines, _ = self.env.run_mysql_query(
                f"SELECT status, COUNT(*) FROM {queue_table} GROUP BY status;"
            )
            if ok:
                for line in lines:
                    parts = [part.strip() for part in line.split("\t")]
                    if len(parts) == 2:
                        queues["status_counts"][parts[0]] = safe_int(parts[1]) or 0
        result["queues"] = queues

        # Search backend health
        engine = self._config_show("catalog/search/engine")
        search_health = {"engine": engine or None}
        if engine in {"elasticsearch7", "elasticsearch8", "opensearch"}:
            host = self._config_show(f"catalog/search/{engine}_server_hostname")
            port = self._config_show(f"catalog/search/{engine}_server_port") or "9200"
            scheme = self._config_show(f"catalog/search/{engine}_server_protocol") or "http"
            if host:
                probe_url = f"{scheme}://{host}:{port}"
                search_health["endpoint"] = probe_url
                search_health["probe"] = self._simple_http_check(probe_url)
        result["search"] = search_health

        # CDN/base asset URL check
        base_static = self._config_show("web/secure/base_static_url") or self._config_show("web/unsecure/base_static_url")
        base_media = self._config_show("web/secure/base_media_url") or self._config_show("web/unsecure/base_media_url")
        result["cdn"] = {
            "base_static_url": base_static or None,
            "base_media_url": base_media or None,
            "configured": bool(base_static or base_media),
        }

        if mode == "developer":
            result["status"] = "critical"
        elif stale_compile or (cron.get("last_24h_error") or 0) > 0 or (cron.get("last_24h_missed") or 0) > 0:
            result["status"] = "warning"

        color = Colors.GREEN if result["status"] == "good" else Colors.ORANGE if result["status"] == "warning" else Colors.RED
        print(
            f"{color}Mode: {mode} | "
            f"Cron errors (24h): {cron.get('last_24h_error')} | "
            f"Cron missed (24h): {cron.get('last_24h_missed')}{Colors.RESET}"
        )
        return result
