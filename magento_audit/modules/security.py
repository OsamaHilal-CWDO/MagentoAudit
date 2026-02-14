"""Security and hardening checks for Magento."""

from __future__ import annotations

import os
import stat
from typing import Dict, Optional

from ..base import Colors, MagentoEnvironment
from ..utils import parse_module_names


class SecurityAuditModule:
    """Review common Magento security posture and risky configuration points."""

    name = "security"

    def __init__(self, env: MagentoEnvironment, runtime_options: Optional[Dict] = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def _config_show(self, path: str) -> str:
        result = self.env.run_magento(f"config:show {path}", timeout=20)
        if result.ok and result.stdout:
            return result.stdout.strip()
        return ""

    def _file_mode(self, path: str) -> Optional[str]:
        if not os.path.exists(path):
            return None
        mode = stat.S_IMODE(os.stat(path).st_mode)
        return oct(mode)

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Running security and configuration audit...{Colors.RESET}")
        result = {"status": "good"}

        custom_admin = self._config_show("admin/url/use_custom")
        admin_path = self._config_show("admin/url/custom")
        result["admin_url"] = {
            "use_custom": custom_admin,
            "custom_path": admin_path if admin_path else None,
            "customized": custom_admin in {"1", "true", "yes"},
        }

        enabled_result = self.env.run_magento("module:status --enabled", timeout=90)
        enabled_modules = parse_module_names(enabled_result.stdout) if enabled_result.ok else []
        two_fa_enabled = "Magento_TwoFactorAuth" in enabled_modules
        result["two_factor_auth"] = {"module_enabled": two_fa_enabled}

        env_php = self.env.env_php_path
        env_mode = self._file_mode(env_php)
        env_world_readable = False
        env_world_writable = False
        if env_mode is not None:
            mode_bits = int(env_mode, 8)
            env_world_readable = bool(mode_bits & stat.S_IROTH)
            env_world_writable = bool(mode_bits & stat.S_IWOTH)
        result["file_permissions"] = {
            "env_php_mode": env_mode,
            "env_php_world_readable": env_world_readable,
            "env_php_world_writable": env_world_writable,
        }

        pub_dir = os.path.join(self.env.root_path, "pub")
        exposed_files = []
        for rel in [".git", "composer.json", "composer.lock", "app/etc/env.php"]:
            path = os.path.join(pub_dir, rel)
            if os.path.exists(path):
                exposed_files.append(path)
        result["webroot_exposure"] = {"exposed_paths": exposed_files}

        risky_keywords = [
            "onepagecheckout",
            "adminlogin",
            "disablecaptcha",
            "payment",
            "paypal",
        ]
        risky_modules = []
        for module in enabled_modules:
            lowered = module.lower()
            if any(keyword in lowered for keyword in risky_keywords):
                risky_modules.append(module)
        result["risky_extension_indicators"] = risky_modules[:50]

        if env_world_writable or exposed_files:
            result["status"] = "critical"
        elif not two_fa_enabled or not result["admin_url"]["customized"]:
            result["status"] = "warning"

        color = Colors.GREEN if result["status"] == "good" else Colors.ORANGE if result["status"] == "warning" else Colors.RED
        print(
            f"{color}2FA enabled: {two_fa_enabled} | "
            f"Custom admin URL: {result['admin_url']['customized']} | "
            f"Exposed webroot files: {len(exposed_files)}{Colors.RESET}"
        )
        return result
