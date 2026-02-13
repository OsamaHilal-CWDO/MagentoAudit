"""Session storage checks for Magento."""

from __future__ import annotations

from typing import Dict
from datetime import datetime, timezone

from ..base import Colors, MagentoEnvironment


class SessionStorageAuditModule:
    """Detect and review Magento session backend (files/db/redis)."""

    name = "sessions"

    def __init__(self, env: MagentoEnvironment, runtime_options: Dict | None = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Reviewing Magento session storage configuration...{Colors.RESET}")

        config = self.env.load_env_config()
        session_config = config.get("session", {}) if isinstance(config, dict) else {}
        save_handler = str(session_config.get("save") or "files").strip().lower()

        result: Dict = {
            "status": "good",
            "save_handler": save_handler,
            "details": {},
        }

        if save_handler == "redis":
            redis_config = session_config.get("redis", {})
            result["details"] = {
                "host": redis_config.get("host"),
                "port": redis_config.get("port"),
                "database": redis_config.get("database"),
                "max_concurrency": redis_config.get("max_concurrency"),
                "disable_locking": redis_config.get("disable_locking"),
                "compression_threshold": redis_config.get("compression_threshold"),
            }
            redis_stats = self._redis_stats(redis_config)
            if redis_stats:
                result["details"]["redis_stats"] = redis_stats
            result["status"] = "good"
            print(
                f"{Colors.GREEN}Session backend: redis "
                f"({result['details'].get('host')}:{result['details'].get('port')}){Colors.RESET}"
            )
        elif save_handler in {"db", "database"}:
            db_metrics = self._database_session_metrics()
            result["details"] = {
                "note": "Magento session data is persisted in MySQL table `session`.",
                "database_metrics": db_metrics,
            }
            result["status"] = "warning"
            print(f"{Colors.ORANGE}Session backend: database{Colors.RESET}")
        else:
            result["details"] = {
                "save_path": session_config.get("save_path"),
                "note": "Filesystem sessions can become a bottleneck under high concurrency.",
            }
            result["status"] = "warning"
            print(f"{Colors.ORANGE}Session backend: files{Colors.RESET}")

        return result

    def _redis_stats(self, redis_config: Dict) -> Dict:
        host = str(redis_config.get("host") or "127.0.0.1")
        port = str(redis_config.get("port") or "6379")
        database = str(redis_config.get("database") or "2")
        command = ["redis-cli", "-h", host, "-p", port, "-n", database, "INFO", "memory"]
        result = self.env.run_command(command, timeout=20)
        if not result.ok:
            return {"error": result.stderr or result.stdout or "redis-cli failed"}

        parsed = {}
        for line in result.stdout.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key in {"used_memory_human", "used_memory_peak_human", "mem_fragmentation_ratio"}:
                parsed[key] = value
        return parsed

    def _database_session_metrics(self) -> Dict:
        prefix = self.env.get_table_prefix()
        table = f"{prefix}session"
        exists_ok, exists_lines, _ = self.env.run_mysql_query(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE table_schema=DATABASE() AND table_name='{table}';"
        )
        exists = bool(exists_ok and exists_lines and exists_lines[0] == "1")
        if not exists:
            return {"table_exists": False}

        now_epoch = int(datetime.now(timezone.utc).timestamp())
        queries = {
            "total_sessions": f"SELECT COUNT(*) FROM {table};",
            "expired_sessions": f"SELECT COUNT(*) FROM {table} WHERE session_expires < {now_epoch};",
        }
        data = {"table_exists": True}
        for key, sql in queries.items():
            ok, lines, _ = self.env.run_mysql_query(sql)
            if ok and lines:
                try:
                    data[key] = int(lines[0])
                except Exception:
                    data[key] = None
        return data
