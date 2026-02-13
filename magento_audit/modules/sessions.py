"""Session storage checks for Magento."""

from __future__ import annotations

from typing import Dict

from ..base import Colors, MagentoEnvironment


class SessionStorageAuditModule:
    """Detect and review Magento session backend (files/db/redis)."""

    name = "sessions"

    def __init__(self, env: MagentoEnvironment):
        self.env = env

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
            result["status"] = "good"
            print(
                f"{Colors.GREEN}Session backend: redis "
                f"({result['details'].get('host')}:{result['details'].get('port')}){Colors.RESET}"
            )
        elif save_handler in {"db", "database"}:
            result["details"] = {
                "note": "Magento session data is persisted in MySQL table `session`."
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
