"""Concurrent capacity estimation for Magento storefront."""

from __future__ import annotations

import concurrent.futures
import statistics
import time
from typing import Dict, List, Optional
from urllib.request import Request, urlopen

from ..base import Colors, MagentoEnvironment


class CapacityLoadTestModule:
    """Estimate concurrent user capacity with guarded load tests."""

    name = "capacity"

    def __init__(self, env: MagentoEnvironment, runtime_options: Optional[Dict] = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def _fetch_once(self, url: str, timeout: int = 20) -> float:
        req = Request(url, headers={"User-Agent": "MagentoHealthAudit/1.0"})
        started = time.time()
        with urlopen(req, timeout=timeout) as response:
            if getattr(response, "status", 200) >= 400:
                raise RuntimeError(f"HTTP {getattr(response, 'status', 500)}")
            response.read(1)
        return (time.time() - started) * 1000

    def _test_level(self, url: str, concurrent_users: int, duration_sec: int) -> Dict:
        start = time.time()
        success = 0
        failures = 0
        response_times: List[float] = []

        def worker() -> Dict:
            local_success = 0
            local_failures = 0
            local_times = []
            while time.time() - start < duration_sec:
                try:
                    elapsed = self._fetch_once(url)
                    local_success += 1
                    local_times.append(elapsed)
                except Exception:
                    local_failures += 1
            return {
                "success": local_success,
                "failures": local_failures,
                "times": local_times,
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_users) as pool:
            futures = [pool.submit(worker) for _ in range(concurrent_users)]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                success += result["success"]
                failures += result["failures"]
                response_times.extend(result["times"])

        total = success + failures
        success_rate = (success / total * 100) if total else 0.0
        avg_response_ms = statistics.mean(response_times) if response_times else None
        status = "good" if success_rate >= 95 and (avg_response_ms or 999999) < 5000 else "warning"
        return {
            "concurrent_users": concurrent_users,
            "duration_sec": duration_sec,
            "successful_requests": success,
            "failed_requests": failures,
            "success_rate_percent": round(success_rate, 2),
            "average_response_ms": round(avg_response_ms, 2) if avg_response_ms is not None else None,
            "status": status,
        }

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Estimating concurrent user capacity...{Colors.RESET}")

        if not bool(self.runtime_options.get("run_capacity_tests", False)):
            print(
                f"{Colors.YELLOW}Capacity load test is disabled by default. "
                "Use --run-capacity-tests to enable active testing."
                f"{Colors.RESET}"
            )
            return {"status": "skipped", "reason": "load_test_disabled"}

        site_url = self.env.resolve_site_url()
        if not site_url:
            return {"status": "skipped", "reason": "site_url_unavailable"}

        levels = self.runtime_options.get("capacity_levels") or [5, 10, 20, 50, 100]
        duration_sec = int(self.runtime_options.get("capacity_duration", 8))

        results = []
        max_healthy = 0
        for level in levels:
            level_result = self._test_level(url=site_url, concurrent_users=int(level), duration_sec=duration_sec)
            results.append(level_result)
            if level_result["status"] == "good":
                max_healthy = int(level)
            else:
                break

        status = "good" if max_healthy >= 50 else "warning" if max_healthy >= 20 else "critical"
        color = Colors.GREEN if status == "good" else Colors.ORANGE if status == "warning" else Colors.RED
        print(
            f"{color}Estimated healthy concurrent users: {max_healthy} "
            f"(levels tested: {len(results)}){Colors.RESET}"
        )
        return {
            "status": status,
            "site_url": site_url,
            "levels_tested": results,
            "estimated_max_healthy_concurrent_users": max_healthy,
        }
