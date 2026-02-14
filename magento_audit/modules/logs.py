"""Log analysis for Magento, PHP-FPM, and web access patterns."""

from __future__ import annotations

import glob
import gzip
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

from ..base import Colors, MagentoEnvironment
from ..utils import safe_float


class LogAnalysisModule:
    """Analyze slow logs, access logs, and Magento exception/system logs."""

    name = "logs"

    def __init__(self, env: MagentoEnvironment, runtime_options: Optional[Dict] = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def _candidate_log_paths(self) -> List[str]:
        base_paths = [self.env.log_path, os.path.join(self.env.root_path, "var", "log"), "/var/log"]
        custom = self.runtime_options.get("log_path")
        if custom:
            base_paths.insert(0, os.path.abspath(custom))
        # Cloudways-style app logs: /home/master/applications/<app_name>/logs
        base_paths.extend(
            [
                path
                for path in glob.glob("/home/master/applications/*/logs")
                if os.path.isdir(path)
            ]
        )
        dedup = []
        for path in base_paths:
            if path and path not in dedup:
                dedup.append(path)
        return dedup

    def _glob_files(self, patterns: Iterable[str]) -> List[str]:
        files: List[str] = []
        for base in self._candidate_log_paths():
            for pattern in patterns:
                files.extend(glob.glob(os.path.join(base, pattern)))
        return sorted(set(files))

    def _open_log(self, path: str):
        if path.endswith(".gz"):
            return gzip.open(path, "rt", errors="ignore")
        return open(path, "r", encoding="utf-8", errors="ignore")

    def _parse_access_datetime(self, line: str) -> Optional[datetime]:
        # Common access format: [14/Feb/2026:07:20:40 +0000]
        match = re.search(r"\[(\d{2}/[A-Za-z]{3}/\d{4}):(\d{2}:\d{2}:\d{2})\s*([+\-]\d{4})?\]", line)
        if match:
            raw = f"{match.group(1)} {match.group(2)}"
            try:
                return datetime.strptime(raw, "%d/%b/%Y %H:%M:%S")
            except Exception:
                pass
        # Alternate plain format: [2026-02-14 07:20:40]
        match = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
        if match:
            try:
                return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        return None

    def _analyze_php_slow_logs(self, days: int = 7) -> Dict:
        patterns = ["*slow*.log*", "php*.slow.log*", "php-fpm*.slow.log*"]
        files = self._glob_files(patterns)
        if not files:
            return {"files": [], "entries": 0, "top_scripts": []}

        cutoff = datetime.now() - timedelta(days=days)
        slow_scripts = defaultdict(lambda: {"count": 0, "total_duration_sec": 0.0, "max_duration_sec": 0.0})
        date_patterns = [
            (re.compile(r"\[(\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}:\d{2})\]"), "%d-%b-%Y %H:%M:%S"),
            (re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"), "%Y-%m-%d %H:%M:%S"),
        ]

        def parse_date(line: str) -> Optional[datetime]:
            for regex, fmt in date_patterns:
                match = regex.search(line)
                if not match:
                    continue
                try:
                    return datetime.strptime(match.group(1), fmt)
                except Exception:
                    continue
            return None

        duration_regex = re.compile(r"(?:duration|executed in)[:=\s]*(\d+(?:\.\d+)?)\s*(ms|msec|s|sec)", re.I)
        script_regex = re.compile(r"(?:script_filename|script)\s*=\s*(\S+)", re.I)

        for path in files:
            try:
                with self._open_log(path) as fp:
                    entry_date = None
                    for raw_line in fp:
                        line = raw_line.strip()
                        parsed_date = parse_date(line)
                        if parsed_date:
                            entry_date = parsed_date
                        if entry_date and entry_date < cutoff:
                            continue

                        script_match = script_regex.search(line)
                        duration_match = duration_regex.search(line)
                        if script_match and duration_match:
                            script = script_match.group(1)
                            value = safe_float(duration_match.group(1)) or 0.0
                            unit = duration_match.group(2).lower()
                            duration_sec = value / 1000 if unit in {"ms", "msec"} else value
                            item = slow_scripts[script]
                            item["count"] += 1
                            item["total_duration_sec"] += duration_sec
                            item["max_duration_sec"] = max(item["max_duration_sec"], duration_sec)
            except Exception:
                continue

        top = []
        for script, data in slow_scripts.items():
            avg = data["total_duration_sec"] / data["count"] if data["count"] else 0.0
            top.append(
                {
                    "script": script,
                    "count": data["count"],
                    "avg_duration_sec": round(avg, 4),
                    "max_duration_sec": round(data["max_duration_sec"], 4),
                    "total_duration_sec": round(data["total_duration_sec"], 4),
                }
            )
        top.sort(key=lambda row: row["total_duration_sec"], reverse=True)
        return {"files": files, "entries": sum(item["count"] for item in top), "top_scripts": top[:15]}

    def _extract_access_metrics(self, line: str) -> Dict[str, object]:
        request_match = re.search(
            r'"(GET|POST|HEAD|PUT|DELETE|OPTIONS|PATCH)\s+([^" ]+)',
            line,
            re.I,
        )
        status_match = re.search(r'"\s+(\d{3})\s+', line)
        request_time_match = re.search(
            r"(?:request_time|upstream_response_time|duration|time)[:=\s]+(\d+(?:\.\d+)?)\s*(ms|msec|s|sec)?",
            line,
            re.I,
        )
        memory_match = re.search(r"(?:memory|rss)[:=\s]+(\d+(?:\.\d+)?)\s*(kb|mb|gb|bytes|b)?", line, re.I)
        cpu_match = re.search(r"(?:cpu|cpu_usage)[:=\s]+(\d+(?:\.\d+)?)\s*%?", line, re.I)

        request_time = None
        if request_time_match:
            value = safe_float(request_time_match.group(1))
            if value is not None:
                unit = (request_time_match.group(2) or "").lower()
                request_time = value / 1000 if unit in {"ms", "msec"} else value

        memory_mb = None
        if memory_match:
            value = safe_float(memory_match.group(1))
            if value is not None:
                unit = (memory_match.group(2) or "").lower()
                if unit in {"gb", "g"}:
                    memory_mb = value * 1024
                elif unit in {"kb", "k"}:
                    memory_mb = value / 1024
                elif unit in {"bytes", "b"}:
                    memory_mb = value / (1024 * 1024)
                else:
                    memory_mb = value

        cpu = safe_float(cpu_match.group(1)) if cpu_match else None
        return {
            "method": request_match.group(1).upper() if request_match else None,
            "path": request_match.group(2).split("?")[0] if request_match else None,
            "status": int(status_match.group(1)) if status_match else None,
            "request_time_sec": request_time,
            "memory_mb": memory_mb,
            "cpu_percent": cpu,
        }

    def _analyze_access_logs(self, days: int = 7) -> Dict:
        patterns = [
            "*access*.log*",
            "nginx*.log*",
            "php*.access.log*",
            "backend_*.access.log*",
        ]
        files = self._glob_files(patterns)
        if not files:
            return {"files": [], "entries": 0}

        cutoff = datetime.now() - timedelta(days=days)
        route_stats = defaultdict(lambda: {"count": 0, "total_time": 0.0, "max_time": 0.0})
        route_groups = defaultdict(lambda: {"count": 0, "total_time": 0.0, "max_time": 0.0})
        errors = defaultdict(int)
        error_urls = defaultdict(lambda: defaultdict(int))
        error_hours = defaultdict(lambda: defaultdict(int))
        method_500 = defaultdict(int)
        response_time_buckets = {"lt_500ms": 0, "500ms_to_1s": 0, "1s_to_2s": 0, "2s_to_5s": 0, "gte_5s": 0}
        memory_samples = []
        cpu_samples = []
        parsed_entries = 0

        for path in files:
            try:
                with self._open_log(path) as fp:
                    for raw_line in fp:
                        line = raw_line.strip()
                        if not line:
                            continue
                        log_datetime = self._parse_access_datetime(line)
                        if log_datetime and log_datetime < cutoff:
                            continue

                        metrics = self._extract_access_metrics(line)
                        if not metrics:
                            continue
                        parsed_entries += 1
                        status = metrics.get("status")
                        if status and status >= 400:
                            status_key = str(status)
                            errors[status_key] += 1
                            path_key = metrics.get("path") or "unknown"
                            error_urls[status_key][path_key] += 1
                            if log_datetime:
                                hour_bucket = log_datetime.strftime("%Y-%m-%d %H:00")
                                error_hours[status_key][hour_bucket] += 1
                            if status == 500 and metrics.get("method"):
                                method_500[str(metrics.get("method"))] += 1

                        path_key = metrics.get("path") or "unknown"
                        route_segment = path_key.strip("/").split("/")[0] if path_key else "root"
                        route_segment = route_segment or "root"
                        req_time = metrics.get("request_time_sec")
                        if req_time and req_time > 0:
                            if req_time < 0.5:
                                response_time_buckets["lt_500ms"] += 1
                            elif req_time < 1:
                                response_time_buckets["500ms_to_1s"] += 1
                            elif req_time < 2:
                                response_time_buckets["1s_to_2s"] += 1
                            elif req_time < 5:
                                response_time_buckets["2s_to_5s"] += 1
                            else:
                                response_time_buckets["gte_5s"] += 1
                            route_stats[path_key]["count"] += 1
                            route_stats[path_key]["total_time"] += req_time
                            route_stats[path_key]["max_time"] = max(route_stats[path_key]["max_time"], req_time)
                            route_groups[route_segment]["count"] += 1
                            route_groups[route_segment]["total_time"] += req_time
                            route_groups[route_segment]["max_time"] = max(route_groups[route_segment]["max_time"], req_time)

                        if metrics.get("memory_mb"):
                            memory_samples.append(metrics["memory_mb"])
                        if metrics.get("cpu_percent") is not None:
                            cpu_samples.append(metrics["cpu_percent"])
            except Exception:
                continue

        top_slow_routes = []
        for route, data in route_stats.items():
            avg = data["total_time"] / data["count"] if data["count"] else 0.0
            top_slow_routes.append(
                {
                    "path": route,
                    "count": data["count"],
                    "avg_time_sec": round(avg, 4),
                    "max_time_sec": round(data["max_time"], 4),
                }
            )
        top_slow_routes.sort(key=lambda row: row["avg_time_sec"], reverse=True)

        top_route_groups = []
        for route, data in route_groups.items():
            avg = data["total_time"] / data["count"] if data["count"] else 0.0
            top_route_groups.append(
                {
                    "route_group": route,
                    "count": data["count"],
                    "avg_time_sec": round(avg, 4),
                    "max_time_sec": round(data["max_time"], 4),
                }
            )
        top_route_groups.sort(key=lambda row: row["avg_time_sec"], reverse=True)

        top_error_urls = {}
        for code, paths in error_urls.items():
            sorted_paths = sorted(paths.items(), key=lambda x: x[1], reverse=True)[:20]
            top_error_urls[code] = [{"path": path, "count": count} for path, count in sorted_paths]

        top_error_hours = {}
        for code, buckets in error_hours.items():
            sorted_hours = sorted(buckets.items(), key=lambda x: x[1], reverse=True)[:20]
            top_error_hours[code] = [{"hour": hour, "count": count} for hour, count in sorted_hours]

        top_500_urls = top_error_urls.get("500", [])
        top_500_hours = top_error_hours.get("500", [])
        total_500 = int(errors.get("500", 0))
        error_rate_500 = round((total_500 / parsed_entries) * 100, 2) if parsed_entries > 0 else None

        return {
            "files": files,
            "entries": parsed_entries,
            "http_errors": dict(errors),
            "top_slow_routes": top_slow_routes[:20],
            "top_route_groups": top_route_groups[:20],
            "top_error_urls": top_error_urls,
            "top_error_hours": top_error_hours,
            "top_500_urls": top_500_urls,
            "top_500_hours": top_500_hours,
            "http_500_by_method": dict(method_500),
            "http_500_error_rate_percent": error_rate_500,
            "response_time_buckets": response_time_buckets,
            "average_memory_mb": round(sum(memory_samples) / len(memory_samples), 2) if memory_samples else None,
            "max_memory_mb": round(max(memory_samples), 2) if memory_samples else None,
            "average_cpu_percent": round(sum(cpu_samples) / len(cpu_samples), 2) if cpu_samples else None,
            "max_cpu_percent": round(max(cpu_samples), 2) if cpu_samples else None,
        }

    def _analyze_magento_app_logs(self, days: int = 7) -> Dict:
        var_log = os.path.join(self.env.root_path, "var", "log")
        files = [
            os.path.join(var_log, "exception.log"),
            os.path.join(var_log, "system.log"),
            os.path.join(var_log, "debug.log"),
        ]

        recurring_exceptions = defaultdict(int)
        exception_types = defaultdict(int)
        exception_hours = defaultdict(int)
        system_levels = defaultdict(int)
        cutoff = datetime.now() - timedelta(days=days)
        timestamp_regex = re.compile(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
        exception_type_regex = re.compile(r"\b([A-Za-z0-9_\\\\]+Exception)\b")

        for path in files:
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fp:
                    for raw_line in fp:
                        line = raw_line.strip()
                        ts_match = timestamp_regex.search(line)
                        if ts_match:
                            try:
                                dt = datetime.strptime(ts_match.group(1), "%Y-%m-%dT%H:%M:%S")
                                if dt < cutoff:
                                    continue
                            except Exception:
                                pass

                        if path.endswith("exception.log"):
                            normalized = re.sub(r"\d+", "?", line)
                            normalized = re.sub(r"'[^']*'", "?", normalized)
                            if normalized:
                                recurring_exceptions[normalized[:220]] += 1
                            type_match = exception_type_regex.search(line)
                            if type_match:
                                exception_types[type_match.group(1)] += 1
                            if ts_match:
                                try:
                                    hour = datetime.strptime(ts_match.group(1), "%Y-%m-%dT%H:%M:%S").strftime(
                                        "%Y-%m-%d %H:00"
                                    )
                                    exception_hours[hour] += 1
                                except Exception:
                                    pass
                        else:
                            if "CRITICAL" in line:
                                system_levels["critical"] += 1
                            elif "ERROR" in line:
                                system_levels["error"] += 1
                            elif "WARNING" in line:
                                system_levels["warning"] += 1
                            elif "INFO" in line:
                                system_levels["info"] += 1
            except Exception:
                continue

        top_exceptions = sorted(
            [{"pattern": key, "count": count} for key, count in recurring_exceptions.items()],
            key=lambda row: row["count"],
            reverse=True,
        )[:15]
        top_exception_types = sorted(
            [{"type": key, "count": count} for key, count in exception_types.items()],
            key=lambda row: row["count"],
            reverse=True,
        )[:15]
        top_exception_hours = sorted(
            [{"hour": key, "count": count} for key, count in exception_hours.items()],
            key=lambda row: row["count"],
            reverse=True,
        )[:12]

        return {
            "files": [path for path in files if os.path.exists(path)],
            "top_exceptions": top_exceptions,
            "top_exception_types": top_exception_types,
            "top_exception_hours": top_exception_hours,
            "system_levels": dict(system_levels),
        }

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Analyzing PHP slow logs, access logs, and Magento app logs...{Colors.RESET}")
        days = int(self.runtime_options.get("log_days", 7))
        result = {
            "status": "good",
            "period_days": days,
            "php_slow_logs": self._analyze_php_slow_logs(days=days),
            "access_logs": self._analyze_access_logs(days=days),
            "magento_logs": self._analyze_magento_app_logs(days=days),
        }

        critical_errors = int(result["access_logs"].get("http_errors", {}).get("500", 0))
        slow_entries = result["php_slow_logs"].get("entries", 0)
        exception_hits = sum(item.get("count", 0) for item in result["magento_logs"].get("top_exceptions", []))

        if critical_errors > 100 or slow_entries > 500:
            result["status"] = "critical"
        elif critical_errors > 20 or slow_entries > 100 or exception_hits > 100:
            result["status"] = "warning"

        color = Colors.GREEN if result["status"] == "good" else Colors.ORANGE if result["status"] == "warning" else Colors.RED
        print(
            f"{color}Slow-log entries: {slow_entries} | "
            f"HTTP 500s: {critical_errors} | "
            f"Exception patterns: {len(result['magento_logs'].get('top_exceptions', []))}{Colors.RESET}"
        )
        top_500_urls = result["access_logs"].get("top_500_urls", [])
        if top_500_urls:
            print(f"{Colors.CYAN}Top URLs returning HTTP 500:{Colors.RESET}")
            for row in top_500_urls[:5]:
                print(f"  - {row.get('count')}x {row.get('path')}")
        top_500_hours = result["access_logs"].get("top_500_hours", [])
        if top_500_hours:
            print(f"{Colors.CYAN}Peak HTTP 500 time buckets (hourly):{Colors.RESET}")
            for row in top_500_hours[:5]:
                print(f"  - {row.get('hour')}: {row.get('count')}")
        top_exception_types = result["magento_logs"].get("top_exception_types", [])
        if top_exception_types:
            print(f"{Colors.CYAN}Top exception types:{Colors.RESET}")
            for row in top_exception_types[:5]:
                print(f"  - {row.get('type')}: {row.get('count')}")
        return result
