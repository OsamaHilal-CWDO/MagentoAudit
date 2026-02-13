#!/usr/bin/env python3
"""Modular Magento health audit focused on performance and core operations."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Dict, List

from magento_audit.base import Colors, MagentoEnvironment
from magento_audit.modules import (
    CacheAuditModule,
    DatabaseAuditModule,
    ExtensionsAuditModule,
    IndexersAuditModule,
    SessionStorageAuditModule,
)


MODULES = {
    "extensions": ExtensionsAuditModule,
    "database": DatabaseAuditModule,
    "indexers": IndexersAuditModule,
    "cache": CacheAuditModule,
    "sessions": SessionStorageAuditModule,
}


class MagentoHealthReportGenerator:
    """Generate and persist a Magento health report."""

    def __init__(self, env: MagentoEnvironment, output_path: str, selected_modules: List[str]):
        self.env = env
        self.output_path = os.path.abspath(output_path)
        self.selected_modules = selected_modules
        self.report: Dict = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "magento_root": self.env.root_path,
            "selected_modules": selected_modules,
            "modules": {},
        }

    def generate(self) -> Dict:
        """Run all selected audit modules and return report."""
        print(f"{Colors.BOLD}{Colors.CYAN}")
        print("=" * 72)
        print("MAGENTO HEALTH AUDIT")
        print("=" * 72)
        print(f"{Colors.RESET}")
        print(f"Magento Root: {self.env.root_path}")
        print(f"Run Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        valid, error = self.env.validate_magento_installation()
        if not valid:
            print(f"{Colors.RED}{error}{Colors.RESET}")
            self.report["error"] = error
            return self.report

        for module_name in self.selected_modules:
            module_cls = MODULES[module_name]
            self.env.print_section(f"{module_name.upper()} AUDIT")
            module = module_cls(self.env)
            self.report["modules"][module_name] = module.run()

        self._print_summary()
        self._save_json_report()
        return self.report

    def _print_summary(self) -> None:
        """Print high-level summary from module statuses."""
        print(f"\n{Colors.BOLD}{Colors.CYAN}")
        print("=" * 72)
        print("EXECUTIVE SUMMARY")
        print("=" * 72)
        print(f"{Colors.RESET}")

        critical = []
        warning = []
        good = []

        for module_name, data in self.report.get("modules", {}).items():
            status = str(data.get("status", "unknown")).lower()
            if status == "critical":
                critical.append(module_name)
            elif status == "warning":
                warning.append(module_name)
            elif status == "good":
                good.append(module_name)

        if critical:
            print(f"{Colors.RED}Critical modules: {', '.join(sorted(critical))}{Colors.RESET}")
        if warning:
            print(f"{Colors.ORANGE}Warning modules: {', '.join(sorted(warning))}{Colors.RESET}")
        if good:
            print(f"{Colors.GREEN}Healthy modules: {', '.join(sorted(good))}{Colors.RESET}")
        if not critical and not warning and not good:
            print(f"{Colors.YELLOW}No module results collected.{Colors.RESET}")

    def _save_json_report(self) -> str:
        """Save report JSON to output path."""
        filename = f"magento_health_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        os.makedirs(self.output_path, exist_ok=True)
        path = os.path.join(self.output_path, filename)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(self.report, fp, indent=2)
        print(f"\n{Colors.GREEN}Report saved to: {path}{Colors.RESET}")
        self.report["report_path"] = path
        return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Magento health audit with modular checks: extensions, database, "
            "indexers, cache, and session storage."
        )
    )
    parser.add_argument(
        "--magento-root",
        default=".",
        help="Path to Magento root directory (default: current directory).",
    )
    parser.add_argument(
        "--output-path",
        default="/mnt/user-data/outputs",
        help="Directory for JSON output report (default: /mnt/user-data/outputs).",
    )
    parser.add_argument(
        "--modules",
        default="extensions,database,indexers,cache,sessions",
        help=(
            "Comma-separated modules to run. Available: "
            + ", ".join(MODULES.keys())
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    requested = [item.strip().lower() for item in args.modules.split(",") if item.strip()]
    invalid = [item for item in requested if item not in MODULES]
    if invalid:
        raise SystemExit(f"Unknown module(s): {', '.join(invalid)}")

    env = MagentoEnvironment(root_path=args.magento_root)
    generator = MagentoHealthReportGenerator(
        env=env,
        output_path=args.output_path,
        selected_modules=requested,
    )
    generator.generate()


if __name__ == "__main__":
    main()
