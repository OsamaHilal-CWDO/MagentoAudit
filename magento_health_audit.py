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
    BackendProfilingModule,
    CacheAuditModule,
    CapacityLoadTestModule,
    DatabaseAuditModule,
    ExtensionProfilerModule,
    ExtensionsAuditModule,
    FrontendAuditModule,
    LogAnalysisModule,
    MagentoHotspotsModule,
    IndexersAuditModule,
    ProductionReadinessModule,
    SecurityAuditModule,
    SessionStorageAuditModule,
)
from magento_audit.recommendations import generate_recommendations


MODULES = {
    "frontend": FrontendAuditModule,
    "extensions": ExtensionsAuditModule,
    "extension_profiler": ExtensionProfilerModule,
    "backend": BackendProfilingModule,
    "logs": LogAnalysisModule,
    "database": DatabaseAuditModule,
    "hotspots": MagentoHotspotsModule,
    "indexers": IndexersAuditModule,
    "cache": CacheAuditModule,
    "sessions": SessionStorageAuditModule,
    "production": ProductionReadinessModule,
    "security": SecurityAuditModule,
    "capacity": CapacityLoadTestModule,
}

PHASE_PRESETS = {
    "phase1": [
        "frontend",
        "extensions",
        "extension_profiler",
        "backend",
        "logs",
        "database",
        "indexers",
        "cache",
        "sessions",
    ],
    "phase2": [
        "frontend",
        "extensions",
        "extension_profiler",
        "backend",
        "logs",
        "database",
        "indexers",
        "cache",
        "sessions",
        "hotspots",
    ],
    "phase3": [
        "frontend",
        "extensions",
        "extension_profiler",
        "backend",
        "logs",
        "database",
        "indexers",
        "cache",
        "sessions",
        "hotspots",
        "production",
        "security",
        "capacity",
    ],
    "all": list(MODULES.keys()),
}


class MagentoHealthReportGenerator:
    """Generate and persist a Magento health report."""

    def __init__(
        self,
        env: MagentoEnvironment,
        output_path: str,
        selected_modules: List[str],
        runtime_options: Dict | None = None,
    ):
        self.env = env
        self.output_path = os.path.abspath(output_path)
        self.selected_modules = selected_modules
        self.runtime_options = runtime_options or {}
        self.report: Dict = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "magento_root": self.env.root_path,
            "site_url": self.env.resolve_site_url(),
            "log_path": self.runtime_options.get("log_path") or self.env.log_path,
            "selected_modules": selected_modules,
            "modules": {},
            "recommendations": [],
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
            module = module_cls(self.env, self.runtime_options)
            self.report["modules"][module_name] = module.run()

        self.report["recommendations"] = generate_recommendations(self.report)
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
        skipped = []

        for module_name, data in self.report.get("modules", {}).items():
            status = str(data.get("status", "unknown")).lower()
            if status == "critical":
                critical.append(module_name)
            elif status == "warning":
                warning.append(module_name)
            elif status == "good":
                good.append(module_name)
            elif status == "skipped":
                skipped.append(module_name)

        if critical:
            print(f"{Colors.RED}Critical modules: {', '.join(sorted(critical))}{Colors.RESET}")
        if warning:
            print(f"{Colors.ORANGE}Warning modules: {', '.join(sorted(warning))}{Colors.RESET}")
        if good:
            print(f"{Colors.GREEN}Healthy modules: {', '.join(sorted(good))}{Colors.RESET}")
        if skipped:
            print(f"{Colors.YELLOW}Skipped modules: {', '.join(sorted(skipped))}{Colors.RESET}")
        if not critical and not warning and not good:
            print(f"{Colors.YELLOW}No module results collected.{Colors.RESET}")

        recommendations = self.report.get("recommendations", [])
        if recommendations:
            print(f"\n{Colors.CYAN}Top recommendations:{Colors.RESET}")
            for item in recommendations[:10]:
                sev = item.get("severity", "medium").upper()
                module = item.get("module")
                title = item.get("title")
                print(f"  - [{sev}] ({module}) {title}")

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
            "indexers, cache, session storage, frontend, logs, and hardening."
        )
    )
    parser.add_argument(
        "--magento-root",
        default=".",
        help="Path to Magento root directory (default: current directory).",
    )
    parser.add_argument(
        "--output-path",
        default="/tmp",
        help="Directory for JSON output report (default: /tmp).",
    )
    parser.add_argument(
        "--site-url",
        default="",
        help="Storefront URL for frontend/cache/capacity checks. Falls back to Magento base URL config.",
    )
    parser.add_argument(
        "--log-path",
        default="",
        help="Path to logs (default: <magento-root>/var/log).",
    )
    parser.add_argument(
        "--phase",
        default="phase1",
        choices=sorted(PHASE_PRESETS.keys()),
        help="Run a preset module phase (default: phase1).",
    )
    parser.add_argument(
        "--modules",
        default="",
        help=(
            "Optional comma-separated modules to run (overrides --phase). Available: "
            + ", ".join(MODULES.keys())
        ),
    )
    parser.add_argument(
        "--ttfb-runs",
        type=int,
        default=5,
        help="Number of TTFB samples for frontend checks (default: 5).",
    )
    parser.add_argument(
        "--throughput-duration",
        type=int,
        default=8,
        help="Throughput test duration in seconds (default: 8).",
    )
    parser.add_argument(
        "--throughput-workers",
        type=int,
        default=5,
        help="Frontend throughput worker count (default: 5).",
    )
    parser.add_argument(
        "--log-days",
        type=int,
        default=7,
        help="Lookback window in days for log analysis (default: 7).",
    )
    parser.add_argument(
        "--run-capacity-tests",
        action="store_true",
        help="Enable active concurrent load testing (disabled by default).",
    )
    parser.add_argument(
        "--capacity-levels",
        default="5,10,20,50,100",
        help="Comma-separated concurrent user levels for capacity tests.",
    )
    parser.add_argument(
        "--capacity-duration",
        type=int,
        default=8,
        help="Per-level duration in seconds for capacity tests (default: 8).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.modules.strip():
        requested = [item.strip().lower() for item in args.modules.split(",") if item.strip()]
    else:
        requested = PHASE_PRESETS[args.phase]
    invalid = [item for item in requested if item not in MODULES]
    if invalid:
        raise SystemExit(f"Unknown module(s): {', '.join(invalid)}")

    capacity_levels = []
    for item in args.capacity_levels.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            capacity_levels.append(int(item))
        except ValueError:
            continue

    runtime_options = {
        "log_path": args.log_path.strip() or None,
        "ttfb_runs": args.ttfb_runs,
        "throughput_duration": args.throughput_duration,
        "throughput_workers": args.throughput_workers,
        "log_days": args.log_days,
        "run_capacity_tests": bool(args.run_capacity_tests),
        "capacity_levels": capacity_levels or [5, 10, 20, 50, 100],
        "capacity_duration": args.capacity_duration,
    }

    env = MagentoEnvironment(
        root_path=args.magento_root,
        site_url=args.site_url.strip() or None,
        log_path=args.log_path.strip() or None,
    )
    generator = MagentoHealthReportGenerator(
        env=env,
        output_path=args.output_path,
        selected_modules=requested,
        runtime_options=runtime_options,
    )
    generator.generate()


if __name__ == "__main__":
    main()
