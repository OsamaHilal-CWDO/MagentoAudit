"""Backend performance profiling for Magento."""

from __future__ import annotations

import glob
import gzip
import json
import os
import re
import statistics
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..base import Colors, MagentoEnvironment
from ..utils import safe_float, safe_int


class BackendProfilingModule:
    """Deep backend profiling: query timing, EAV overhead, and slow query insights."""

    name = "backend"

    def __init__(self, env: MagentoEnvironment, runtime_options: Optional[Dict] = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def _table_exists(self, table_name: str) -> bool:
        ok, lines, _ = self.env.run_mysql_query(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE table_schema = DATABASE() AND table_name = '{table_name}';"
        )
        return bool(ok and lines and (safe_int(lines[0]) or 0) == 1)

    def _timed_query(self, name: str, sql: str, runs: int = 3) -> Dict:
        timings: List[float] = []
        errors: List[str] = []
        for _ in range(runs):
            started = time.time()
            ok, _, err = self.env.run_mysql_query(sql)
            elapsed_ms = (time.time() - started) * 1000
            if ok:
                timings.append(elapsed_ms)
            else:
                errors.append(err or "query_failed")

        avg_ms = round(statistics.mean(timings), 2) if timings else None
        status = "good"
        if avg_ms is not None:
            status = "good" if avg_ms < 100 else "warning" if avg_ms < 500 else "critical"
        if errors and not timings:
            status = "error"

        explain_summary = self._explain_query(sql)
        return {
            "name": name,
            "sql": sql,
            "avg_ms": avg_ms,
            "min_ms": round(min(timings), 2) if timings else None,
            "max_ms": round(max(timings), 2) if timings else None,
            "samples": len(timings),
            "status": status,
            "errors": errors,
            "explain": explain_summary,
        }

    def _extract_plan_table_nodes(self, node: Dict, nodes: List[Dict]) -> None:
        if not isinstance(node, dict):
            return
        table_name = node.get("table_name")
        access_type = node.get("access_type")
        rows = node.get("rows_examined_per_scan")
        if table_name or access_type:
            nodes.append(
                {
                    "table": table_name,
                    "access_type": access_type,
                    "rows_examined_per_scan": rows,
                    "using_temporary_table": bool(node.get("using_temporary_table")),
                    "using_filesort": bool(node.get("using_filesort")),
                }
            )
        for value in node.values():
            if isinstance(value, dict):
                self._extract_plan_table_nodes(value, nodes)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._extract_plan_table_nodes(item, nodes)

    def _explain_query(self, sql: str) -> Dict:
        ok, lines, err = self.env.run_mysql_query(f"EXPLAIN FORMAT=JSON {sql}")
        if not ok or not lines:
            return {"error": err or "explain_failed"}
        try:
            payload = json.loads(lines[0])
        except Exception:
            return {"raw": lines[0]}

        nodes: List[Dict] = []
        self._extract_plan_table_nodes(payload, nodes)
        scans = []
        for node in nodes:
            access = (node.get("access_type") or "").upper()
            rows = node.get("rows_examined_per_scan") or 0
            if access == "ALL" and rows and rows > 10000:
                scans.append(node)
        return {"tables": nodes, "full_scans": scans}

    def _log_files(self) -> List[str]:
        paths = []
        configured = self.runtime_options.get("log_path") or self.env.log_path
        if configured:
            paths.append(os.path.abspath(configured))
        # Cloudways-style app logs: /home/master/applications/<app_name>/logs
        paths.extend(
            [
                path
                for path in glob.glob("/home/master/applications/*/logs")
                if os.path.isdir(path)
            ]
        )
        paths.extend(
            [
                "/var/log/mysql",
                "/var/log",
            ]
        )
        patterns = ["*slow*.log*", "mysql-slow.log*", "mariadb-slow.log*"]
        files = []
        for path in paths:
            for pattern in patterns:
                files.extend(glob.glob(os.path.join(path, pattern)))
        return sorted(set(files))

    def _fingerprint_sql(self, sql: str) -> str:
        normalized = re.sub(r"'[^']*'", "?", sql)
        normalized = re.sub(r'"[^"]*"', "?", normalized)
        normalized = re.sub(r"\b\d+\b", "?", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized[:240]

    def _parse_mysql_slow_logs(self, days: int = 7, top_n: int = 10) -> Dict:
        cutoff = datetime.now() - timedelta(days=days)
        time_regex = re.compile(r"^# Time:\s+(\d{6}\s+\d{2}:\d{2}:\d{2})")
        query_time_regex = re.compile(r"# Query_time:\s*([0-9.]+)")
        date_formats = ["%y%m%d %H:%M:%S"]

        totals = {
            "entries": 0,
            "timed_entries": 0,
            "total_query_time_sec": 0.0,
        }
        fingerprints: Dict[str, Dict] = {}

        for log_file in self._log_files():
            try:
                if log_file.endswith(".gz"):
                    handler = gzip.open(log_file, "rt", errors="ignore")
                else:
                    handler = open(log_file, "r", encoding="utf-8", errors="ignore")
            except Exception:
                continue

            with handler as fp:
                current_time: Optional[datetime] = None
                current_query_time: Optional[float] = None
                current_query_lines: List[str] = []

                def flush_entry() -> None:
                    nonlocal current_time, current_query_time, current_query_lines
                    query = " ".join(line.strip() for line in current_query_lines if line.strip())
                    if not query:
                        current_query_time = None
                        current_query_lines = []
                        return
                    if current_time and current_time < cutoff:
                        current_query_time = None
                        current_query_lines = []
                        return

                    totals["entries"] += 1
                    if current_query_time is not None:
                        totals["timed_entries"] += 1
                        totals["total_query_time_sec"] += current_query_time

                    fingerprint = self._fingerprint_sql(query)
                    item = fingerprints.setdefault(
                        fingerprint,
                        {
                            "fingerprint": fingerprint,
                            "count": 0,
                            "timed_count": 0,
                            "total_query_time_sec": 0.0,
                            "max_query_time_sec": 0.0,
                        },
                    )
                    item["count"] += 1
                    if current_query_time is not None:
                        item["timed_count"] += 1
                        item["total_query_time_sec"] += current_query_time
                        item["max_query_time_sec"] = max(
                            item["max_query_time_sec"], current_query_time
                        )
                    current_query_time = None
                    current_query_lines = []

                for raw_line in fp:
                    line = raw_line.rstrip("\n")
                    time_match = time_regex.match(line)
                    if time_match:
                        flush_entry()
                        parsed = None
                        for fmt in date_formats:
                            try:
                                parsed = datetime.strptime(time_match.group(1), fmt)
                                break
                            except Exception:
                                continue
                        current_time = parsed
                        continue

                    query_time_match = query_time_regex.search(line)
                    if query_time_match:
                        current_query_time = safe_float(query_time_match.group(1))
                        continue

                    if line.startswith("#"):
                        continue
                    if line.startswith("SET timestamp="):
                        continue
                    if line.strip():
                        current_query_lines.append(line)
                        if line.strip().endswith(";"):
                            flush_entry()

                flush_entry()

        top = sorted(
            fingerprints.values(),
            key=lambda x: (x["total_query_time_sec"], x["count"]),
            reverse=True,
        )[:top_n]
        for item in top:
            timed_count = item.get("timed_count", 0)
            avg = (
                item["total_query_time_sec"] / timed_count
                if timed_count and item["total_query_time_sec"] > 0
                else None
            )
            item["avg_query_time_sec"] = round(avg, 4) if avg is not None else None
            item["max_query_time_sec"] = round(item["max_query_time_sec"], 4)
            item["total_query_time_sec"] = round(item["total_query_time_sec"], 4)

        return {
            "period_days": days,
            "files_considered": self._log_files(),
            "entries": totals["entries"],
            "timed_entries": totals["timed_entries"],
            "total_query_time_sec": round(totals["total_query_time_sec"], 4),
            "top_slow_fingerprints": top,
        }

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Profiling backend query performance and EAV overhead...{Colors.RESET}")

        ok, _, err = self.env.run_mysql_query("SELECT 1;")
        if not ok:
            print(f"{Colors.RED}Database check failed: {err}{Colors.RESET}")
            return {"status": "error", "error": err}

        prefix = self.env.get_table_prefix()
        runs = int(self.runtime_options.get("query_runs", 3))
        days = int(self.runtime_options.get("log_days", 7))
        result: Dict = {
            "status": "good",
            "query_benchmarks": [],
            "catalog_correlation": {},
            "eav_overhead": {},
            "flat_catalog": {},
            "config_payload": {},
            "slow_query_log": {},
        }

        benchmark_queries = [
            (
                "Product Count",
                f"SELECT COUNT(*) FROM {prefix}catalog_product_entity",
            ),
            (
                "Category Count",
                f"SELECT COUNT(*) FROM {prefix}catalog_category_entity",
            ),
            (
                "Order Count",
                f"SELECT COUNT(*) FROM {prefix}sales_order",
            ),
            (
                "Config Read",
                f"SELECT value FROM {prefix}core_config_data "
                "WHERE path='web/secure/base_url' LIMIT 1",
            ),
            (
                "Product Attribute Join",
                f"SELECT e.entity_id FROM {prefix}catalog_product_entity e "
                f"LEFT JOIN {prefix}catalog_product_entity_varchar v "
                f"ON e.entity_id=v.entity_id LIMIT 500",
            ),
            (
                "Order Grid Recent",
                f"SELECT entity_id FROM {prefix}sales_order_grid "
                "ORDER BY entity_id DESC LIMIT 200",
            ),
        ]
        for name, sql in benchmark_queries:
            table_hint = sql.split("FROM ")[1].split()[0] if "FROM " in sql else ""
            if table_hint and not self._table_exists(table_hint):
                continue
            result["query_benchmarks"].append(self._timed_query(name=name, sql=sql, runs=runs))

        # Catalog size correlation metrics
        metric_queries = {
            "product_count": f"SELECT COUNT(*) FROM {prefix}catalog_product_entity",
            "category_count": f"SELECT COUNT(*) FROM {prefix}catalog_category_entity",
            "customer_count": f"SELECT COUNT(*) FROM {prefix}customer_entity",
            "order_count": f"SELECT COUNT(*) FROM {prefix}sales_order",
            "url_rewrite_count": f"SELECT COUNT(*) FROM {prefix}url_rewrite",
        }
        for key, sql in metric_queries.items():
            table_name = sql.split("FROM ")[1].split()[0]
            if not self._table_exists(table_name):
                continue
            metric_ok, lines, _ = self.env.run_mysql_query(sql)
            if metric_ok and lines:
                result["catalog_correlation"][key] = safe_int(lines[0])

        # EAV overhead analysis
        eav_queries = {
            "product_attributes_total": (
                f"SELECT COUNT(*) FROM {prefix}eav_attribute ea "
                f"JOIN {prefix}eav_entity_type et ON et.entity_type_id = ea.entity_type_id "
                "WHERE et.entity_type_code='catalog_product';"
            ),
            "product_attributes_user_defined": (
                f"SELECT COUNT(*) FROM {prefix}eav_attribute ea "
                f"JOIN {prefix}eav_entity_type et ON et.entity_type_id = ea.entity_type_id "
                "WHERE et.entity_type_code='catalog_product' AND ea.is_user_defined=1;"
            ),
            "category_attributes_total": (
                f"SELECT COUNT(*) FROM {prefix}eav_attribute ea "
                f"JOIN {prefix}eav_entity_type et ON et.entity_type_id = ea.entity_type_id "
                "WHERE et.entity_type_code='catalog_category';"
            ),
        }
        for key, sql in eav_queries.items():
            eav_ok, lines, _ = self.env.run_mysql_query(sql)
            if eav_ok and lines:
                result["eav_overhead"][key] = safe_int(lines[0])

        varchar_table = f"{prefix}catalog_product_entity_varchar"
        if self._table_exists(varchar_table):
            ok_total, total_lines, _ = self.env.run_mysql_query(f"SELECT COUNT(*) FROM {varchar_table};")
            ok_empty, empty_lines, _ = self.env.run_mysql_query(
                f"SELECT COUNT(*) FROM {varchar_table} WHERE value='' OR value IS NULL;"
            )
            total = safe_int(total_lines[0]) if ok_total and total_lines else None
            empty = safe_int(empty_lines[0]) if ok_empty and empty_lines else None
            ratio = round((empty / total) * 100, 2) if total and empty is not None else None
            result["eav_overhead"]["varchar_rows_total"] = total
            result["eav_overhead"]["varchar_rows_empty"] = empty
            result["eav_overhead"]["varchar_empty_ratio_percent"] = ratio

        # Flat catalog (legacy performance setting in Magento 2, generally deprecated).
        flat_product = self.env.run_magento("config:show catalog/frontend/flat_catalog_product", timeout=20)
        flat_category = self.env.run_magento("config:show catalog/frontend/flat_catalog_category", timeout=20)
        result["flat_catalog"] = {
            "flat_catalog_product": flat_product.stdout.strip() if flat_product.ok and flat_product.stdout else "",
            "flat_catalog_category": flat_category.stdout.strip() if flat_category.ok and flat_category.stdout else "",
            "note": "Flat catalog is legacy/deprecated in modern Magento 2 versions.",
        }

        # "Autoload-like" payload check for frequently loaded config rows.
        config_table = f"{prefix}core_config_data"
        if self._table_exists(config_table):
            cfg_ok, cfg_lines, _ = self.env.run_mysql_query(
                f"SELECT ROUND(SUM(LENGTH(value)) / 1024 / 1024, 2) FROM {config_table};"
            )
            if cfg_ok and cfg_lines:
                result["config_payload"]["core_config_data_total_mb"] = safe_float(cfg_lines[0])

        result["slow_query_log"] = self._parse_mysql_slow_logs(days=days, top_n=10)

        any_critical_query = any(item.get("status") == "critical" for item in result["query_benchmarks"])
        product_attrs = result.get("eav_overhead", {}).get("product_attributes_total") or 0
        empty_ratio = result.get("eav_overhead", {}).get("varchar_empty_ratio_percent") or 0
        timed_entries = result.get("slow_query_log", {}).get("timed_entries") or 0

        if any_critical_query:
            result["status"] = "critical"
        elif product_attrs > 300 or empty_ratio > 30 or timed_entries > 500:
            result["status"] = "warning"

        color = Colors.GREEN if result["status"] == "good" else Colors.ORANGE if result["status"] == "warning" else Colors.RED
        print(
            f"{color}Benchmarked queries: {len(result['query_benchmarks'])} | "
            f"Product attributes: {product_attrs} | "
            f"Slow-log entries: {timed_entries}{Colors.RESET}"
        )
        return result
