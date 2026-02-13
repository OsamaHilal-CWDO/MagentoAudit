"""Static and runtime extension impact profiling for Magento."""

from __future__ import annotations

import os
import re
import statistics
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from ..base import Colors, MagentoEnvironment
from ..utils import parse_module_names


class ExtensionProfilerModule:
    """Estimate extension impact using XML observer/plugin footprint and CLI timing."""

    name = "extension_profiler"

    def __init__(self, env: MagentoEnvironment, runtime_options: Optional[Dict] = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def _is_third_party(self, module_name: str) -> bool:
        return not module_name.startswith(("Magento_", "Laminas_", "Zend_"))

    def _discover_module_paths(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        roots = [os.path.join(self.env.root_path, "app", "code"), os.path.join(self.env.root_path, "vendor")]
        regex = re.compile(r"['\"]([A-Za-z0-9_]+)['\"]")

        for root in roots:
            if not os.path.isdir(root):
                continue
            for current_root, _, files in os.walk(root):
                if "registration.php" not in files:
                    continue
                reg_path = os.path.join(current_root, "registration.php")
                try:
                    with open(reg_path, "r", encoding="utf-8", errors="ignore") as fp:
                        content = fp.read()
                except Exception:
                    continue
                if "ComponentRegistrar::MODULE" not in content:
                    continue
                matches = regex.findall(content)
                module_name = None
                for candidate in matches:
                    if "_" in candidate and candidate[0].isalpha():
                        module_name = candidate
                        break
                if module_name:
                    mapping[module_name] = current_root
        return mapping

    def _safe_parse_xml(self, path: str) -> Optional[ET.Element]:
        try:
            return ET.parse(path).getroot()
        except Exception:
            return None

    def _scan_extension_complexity(self, module_path: str) -> Dict:
        observers = 0
        plugins = 0
        preferences = 0
        total_xml_files = 0
        total_php_files = 0
        code_size_kb = 0.0

        for current_root, _, files in os.walk(module_path):
            for filename in files:
                path = os.path.join(current_root, filename)
                if filename.endswith(".xml"):
                    total_xml_files += 1
                    root = self._safe_parse_xml(path)
                    if root is not None:
                        observers += len(root.findall(".//observer"))
                        plugins += len(root.findall(".//plugin"))
                        preferences += len(root.findall(".//preference"))
                if filename.endswith(".php"):
                    total_php_files += 1
                try:
                    code_size_kb += os.path.getsize(path) / 1024
                except Exception:
                    continue

        impact_score = (observers * 2) + plugins + (preferences * 3)
        return {
            "observers": observers,
            "plugins": plugins,
            "preferences": preferences,
            "xml_files": total_xml_files,
            "php_files": total_php_files,
            "code_size_kb": round(code_size_kb, 2),
            "impact_score": impact_score,
        }

    def _benchmark_cli_bootstrap(self, runs: int = 3) -> Dict:
        samples = []
        for _ in range(runs):
            started = time.time()
            result = self.env.run_magento("cache:status", timeout=120)
            if not result.ok:
                return {"error": result.stderr or result.stdout or "cache:status failed"}
            samples.append((time.time() - started) * 1000)
        return {
            "samples": len(samples),
            "average_ms": round(statistics.mean(samples), 2),
            "min_ms": round(min(samples), 2),
            "max_ms": round(max(samples), 2),
        }

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Profiling extension impact and observer/plugin load...{Colors.RESET}")

        enabled_result = self.env.run_magento("module:status --enabled", timeout=120)
        if not enabled_result.ok:
            error = enabled_result.stderr or enabled_result.stdout or "module:status failed"
            print(f"{Colors.RED}Unable to profile extensions: {error}{Colors.RESET}")
            return {"status": "error", "error": error}

        enabled = parse_module_names(enabled_result.stdout)
        third_party_enabled = [module for module in enabled if self._is_third_party(module)]
        path_map = self._discover_module_paths()

        profile_rows = []
        for module_name in third_party_enabled:
            module_path = path_map.get(module_name)
            if not module_path or not os.path.isdir(module_path):
                continue
            complexity = self._scan_extension_complexity(module_path)
            profile_rows.append(
                {
                    "module": module_name,
                    "path": module_path,
                    **complexity,
                }
            )

        profile_rows.sort(key=lambda row: row["impact_score"], reverse=True)
        top_n = int(self.runtime_options.get("extension_top_n", 20))
        top_rows = profile_rows[:top_n]

        cli_benchmark = self._benchmark_cli_bootstrap(runs=int(self.runtime_options.get("extension_profile_runs", 3)))

        status = "good"
        if top_rows and top_rows[0].get("impact_score", 0) > 150:
            status = "warning"
        if isinstance(cli_benchmark, dict) and (cli_benchmark.get("average_ms") or 0) > 3000:
            status = "warning"

        color = Colors.GREEN if status == "good" else Colors.ORANGE
        print(
            f"{color}Third-party modules: {len(third_party_enabled)} | "
            f"Profiled modules: {len(profile_rows)} | "
            f"Top impact score: {top_rows[0]['impact_score'] if top_rows else 0}{Colors.RESET}"
        )

        return {
            "status": status,
            "third_party_enabled_count": len(third_party_enabled),
            "profiled_modules_count": len(profile_rows),
            "top_extensions": top_rows,
            "cli_bootstrap_benchmark": cli_benchmark,
            "limitations": [
                "Safe mode only: this profiler does not disable modules in production.",
                "Impact score is heuristic based on observers/plugins/preferences and code footprint.",
            ],
        }
