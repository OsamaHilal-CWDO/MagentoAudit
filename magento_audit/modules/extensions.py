"""Magento extension/module validation checks."""

from __future__ import annotations

from typing import Dict, List

from ..base import Colors, MagentoEnvironment
from ..utils import parse_module_names


REQUIRED_CORE_MODULES = [
    "Magento_Backend",
    "Magento_Store",
    "Magento_Config",
    "Magento_Indexer",
    "Magento_Catalog",
    "Magento_Customer",
    "Magento_Sales",
    "Magento_Checkout",
    "Magento_PageCache",
]


class ExtensionsAuditModule:
    """Review module/extension status and highlight risky states."""

    name = "extensions"

    def __init__(self, env: MagentoEnvironment, runtime_options: Dict | None = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def _is_third_party(self, module_name: str) -> bool:
        core_prefixes = ("Magento_", "Laminas_", "Zend_")
        return not module_name.startswith(core_prefixes)

    def _status_from_counts(self, critical_disabled: List[str], third_party_count: int) -> str:
        if critical_disabled:
            return "critical"
        if third_party_count > 120:
            return "warning"
        return "good"

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Reviewing Magento extensions/modules...{Colors.RESET}")

        enabled_result = self.env.run_magento("module:status --enabled", timeout=90)
        disabled_result = self.env.run_magento("module:status --disabled", timeout=90)

        if not enabled_result.ok and not disabled_result.ok:
            error = enabled_result.stderr or disabled_result.stderr or "module:status failed"
            print(f"{Colors.RED}Unable to read module status: {error}{Colors.RESET}")
            return {"status": "error", "error": error}

        enabled_modules = parse_module_names(enabled_result.stdout)
        disabled_modules = parse_module_names(disabled_result.stdout)
        third_party_enabled = [m for m in enabled_modules if self._is_third_party(m)]
        critical_disabled = [m for m in REQUIRED_CORE_MODULES if m in disabled_modules]

        status = self._status_from_counts(critical_disabled, len(third_party_enabled))
        status_color = (
            Colors.GREEN if status == "good" else Colors.ORANGE if status == "warning" else Colors.RED
        )

        print(
            f"{status_color}Enabled modules: {len(enabled_modules)} | "
            f"Disabled modules: {len(disabled_modules)} | "
            f"Third-party enabled: {len(third_party_enabled)}{Colors.RESET}"
        )
        if critical_disabled:
            print(f"{Colors.RED}Critical core modules disabled: {', '.join(critical_disabled)}{Colors.RESET}")

        return {
            "status": status,
            "enabled_count": len(enabled_modules),
            "disabled_count": len(disabled_modules),
            "third_party_enabled_count": len(third_party_enabled),
            "critical_core_modules_disabled": critical_disabled,
            "third_party_enabled_top_50": sorted(third_party_enabled)[:50],
            "enabled_modules": enabled_modules,
            "disabled_modules": disabled_modules,
        }
