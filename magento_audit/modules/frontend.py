"""Frontend performance checks for Magento storefront pages."""

from __future__ import annotations

import concurrent.futures
import re
import statistics
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from ..base import Colors, MagentoEnvironment
from ..utils import safe_int


class FrontendAuditModule:
    """Measure key frontend performance indicators."""

    name = "frontend"

    def __init__(self, env: MagentoEnvironment, runtime_options: Optional[Dict] = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def _status_from_thresholds(
        self,
        value: Optional[float],
        good_limit: float,
        warn_limit: float,
    ) -> str:
        if value is None:
            return "unknown"
        if value < good_limit:
            return "good"
        if value < warn_limit:
            return "warning"
        return "critical"

    def _fetch_url(self, url: str, timeout: int = 30) -> Dict:
        req = Request(url, headers={"User-Agent": "MagentoHealthAudit/1.0"})
        started = time.time()
        with urlopen(req, timeout=timeout) as response:
            # Measure TTFB as time to fetch first byte.
            first_byte = response.read(1)
            ttfb_ms = (time.time() - started) * 1000
            body = first_byte + response.read()
            total_ms = (time.time() - started) * 1000
            return {
                "status_code": getattr(response, "status", 200),
                "ttfb_ms": round(ttfb_ms, 2),
                "total_ms": round(total_ms, 2),
                "size_bytes": len(body),
                "body_preview": body[:200000].decode("utf-8", errors="ignore"),
                "headers": dict(response.headers.items()),
            }

    def _discover_page_paths(self) -> Dict[str, Optional[str]]:
        prefix = self.env.get_table_prefix()
        table = f"{prefix}url_rewrite"

        result = {
            "home": "",
            "category": None,
            "product": None,
            "checkout": "checkout",
        }

        exists_ok, exists_lines, _ = self.env.run_mysql_query(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE table_schema = DATABASE() AND table_name = '{table}';"
        )
        exists = bool(exists_ok and exists_lines and (safe_int(exists_lines[0]) or 0) == 1)
        if not exists:
            return result

        category_query = (
            f"SELECT request_path FROM {table} "
            "WHERE entity_type='category' AND redirect_type=0 "
            "ORDER BY url_rewrite_id DESC LIMIT 1;"
        )
        product_query = (
            f"SELECT request_path FROM {table} "
            "WHERE entity_type='product' AND redirect_type=0 "
            "ORDER BY url_rewrite_id DESC LIMIT 1;"
        )

        ok, lines, _ = self.env.run_mysql_query(category_query)
        if ok and lines:
            result["category"] = lines[0].lstrip("/")
        ok, lines, _ = self.env.run_mysql_query(product_query)
        if ok and lines:
            result["product"] = lines[0].lstrip("/")
        return result

    def _measure_ttfb(self, url: str, runs: int = 5) -> Dict:
        values: List[float] = []
        errors = 0
        for _ in range(runs):
            try:
                sample = self._fetch_url(url, timeout=30)
                values.append(sample.get("ttfb_ms", 0.0))
                time.sleep(0.2)
            except Exception:
                errors += 1

        avg = round(statistics.mean(values), 2) if values else None
        return {
            "average_ms": avg,
            "min_ms": round(min(values), 2) if values else None,
            "max_ms": round(max(values), 2) if values else None,
            "samples": len(values),
            "errors": errors,
            "status": self._status_from_thresholds(avg, 600, 1000),
        }

    def _measure_page(self, label: str, url: str) -> Dict:
        try:
            sample = self._fetch_url(url, timeout=40)
            html = sample.get("body_preview") or ""
            css_count = len(re.findall(r"<link[^>]*rel=[\"']stylesheet[\"']", html, flags=re.I))
            js_count = len(re.findall(r"<script[^>]*src=", html, flags=re.I))
            img_count = len(re.findall(r"<img[^>]*src=", html, flags=re.I))
            total_resources = css_count + js_count + img_count + 1
            page_size_mb = sample["size_bytes"] / (1024 * 1024)
            return {
                "label": label,
                "url": url,
                "status_code": sample.get("status_code"),
                "page_load_ms": sample.get("total_ms"),
                "ttfb_ms": sample.get("ttfb_ms"),
                "page_size_kb": round(sample["size_bytes"] / 1024, 2),
                "page_size_mb": round(page_size_mb, 2),
                "resource_counts": {
                    "css": css_count,
                    "js": js_count,
                    "images": img_count,
                    "total": total_resources,
                },
                "status": self._status_from_thresholds(sample.get("total_ms"), 2500, 5000),
            }
        except Exception as exc:
            return {"label": label, "url": url, "status": "error", "error": str(exc)}

    def _measure_throughput(self, url: str, duration: int = 8, workers: int = 5) -> Dict:
        start = time.time()
        success = 0
        failures = 0
        total_time_ms = 0.0
        total_time_samples = 0

        def worker() -> Tuple[int, int, float, int]:
            run_start = time.time()
            local_success = 0
            local_failures = 0
            local_total_time_ms = 0.0
            local_time_samples = 0
            while time.time() - run_start < duration:
                req_start = time.time()
                try:
                    sample = self._fetch_url(url, timeout=20)
                    if int(sample.get("status_code", 500)) < 400:
                        local_success += 1
                        local_total_time_ms += (time.time() - req_start) * 1000
                        local_time_samples += 1
                    else:
                        local_failures += 1
                except Exception:
                    local_failures += 1
            return local_success, local_failures, local_total_time_ms, local_time_samples

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker) for _ in range(workers)]
            for future in concurrent.futures.as_completed(futures):
                worker_success, worker_failures, worker_total_time, worker_samples = future.result()
                success += worker_success
                failures += worker_failures
                total_time_ms += worker_total_time
                total_time_samples += worker_samples

        elapsed = time.time() - start
        req_per_sec = round(success / elapsed, 2) if elapsed > 0 else 0.0
        avg_resp = round(total_time_ms / total_time_samples, 2) if total_time_samples > 0 else None
        status = "good" if req_per_sec > 8 else "warning" if req_per_sec > 4 else "critical"
        return {
            "workers": workers,
            "duration_sec": duration,
            "requests_per_second_estimate": req_per_sec,
            "successful_requests": success,
            "failed_requests": failures,
            "average_response_ms": avg_resp,
            "status": status,
        }

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Measuring frontend performance metrics...{Colors.RESET}")
        site_url = self.env.resolve_site_url()
        if not site_url:
            print(
                f"{Colors.YELLOW}Site URL is not configured; skipping frontend checks. "
                "Use --site-url to enable this module fully."
                f"{Colors.RESET}"
            )
            return {"status": "skipped", "reason": "site_url_unavailable"}

        paths = self._discover_page_paths()
        runs = int(self.runtime_options.get("ttfb_runs", 5))
        throughput_duration = int(self.runtime_options.get("throughput_duration", 8))
        throughput_workers = int(self.runtime_options.get("throughput_workers", 5))

        pages = {}
        for key, path in paths.items():
            if path is None:
                continue
            url = urljoin(site_url, path)
            pages[key] = self._measure_page(key, url)

        ttfb = self._measure_ttfb(urljoin(site_url, paths.get("home", "")), runs=runs)
        throughput = self._measure_throughput(
            urljoin(site_url, paths.get("home", "")),
            duration=throughput_duration,
            workers=throughput_workers,
        )

        statuses = [ttfb.get("status"), throughput.get("status")]
        statuses.extend(page.get("status") for page in pages.values() if page.get("status"))
        overall = "good"
        if any(item == "critical" for item in statuses):
            overall = "critical"
        elif any(item == "warning" for item in statuses):
            overall = "warning"

        color = Colors.GREEN if overall == "good" else Colors.ORANGE if overall == "warning" else Colors.RED
        print(
            f"{color}TTFB: {ttfb.get('average_ms')}ms | "
            f"Throughput: {throughput.get('requests_per_second_estimate')} rps | "
            f"Pages tested: {len(pages)}{Colors.RESET}"
        )

        return {
            "status": overall,
            "site_url": site_url,
            "ttfb": ttfb,
            "pages": pages,
            "throughput": throughput,
        }
