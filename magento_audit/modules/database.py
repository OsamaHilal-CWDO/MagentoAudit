"""Database performance and index coverage checks for Magento."""

from __future__ import annotations

from typing import Dict, List

from ..base import Colors, MagentoEnvironment
from ..utils import safe_float, safe_int


class DatabaseAuditModule:
    """Review DB sizing, MySQL settings, and key index coverage."""

    name = "database"

    def __init__(self, env: MagentoEnvironment, runtime_options: Dict | None = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def _parse_tab_row(self, line: str) -> List[str]:
        return [part.strip() for part in line.split("\t")]

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Reviewing database performance and index health...{Colors.RESET}")

        ping_ok, ping_lines, ping_error = self.env.run_mysql_query("SELECT 1;")
        if not ping_ok:
            print(f"{Colors.RED}Database check failed: {ping_error}{Colors.RESET}")
            return {"status": "error", "error": ping_error}

        table_prefix = self.env.get_table_prefix()

        result: Dict = {
            "status": "good",
            "connectivity": {"ok": True, "probe_result": ping_lines[0] if ping_lines else "1"},
            "table_prefix": table_prefix,
            "database_size": {},
            "largest_tables": [],
            "mysql_runtime": {},
            "index_health": {},
            "table_hotspots": [],
            "fragmentation": [],
            "foreign_keys": {},
            "orphaned_data": {},
            "eav_bloat": {},
            "session_cleanup": {},
            "retention": {},
            "query_explain_warnings": [],
            "log_related_tables": [],
        }

        # DB size and table count
        db_size_query = (
            "SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2), COUNT(*) "
            "FROM information_schema.TABLES WHERE table_schema = DATABASE();"
        )
        ok, lines, error = self.env.run_mysql_query(db_size_query)
        if ok and lines:
            parts = self._parse_tab_row(lines[0])
            size_mb = safe_float(parts[0]) if len(parts) > 0 else None
            table_count = safe_int(parts[1]) if len(parts) > 1 else None
            result["database_size"] = {
                "size_mb": size_mb,
                "size_gb": round((size_mb / 1024), 2) if size_mb is not None else None,
                "table_count": table_count,
            }
        elif error:
            result["database_size"] = {"error": error}

        # Largest tables by total size
        top_tables_query = (
            "SELECT table_name, ROUND((data_length + index_length) / 1024 / 1024, 2), "
            "table_rows, engine "
            "FROM information_schema.TABLES WHERE table_schema = DATABASE() "
            "ORDER BY (data_length + index_length) DESC LIMIT 15;"
        )
        ok, lines, _ = self.env.run_mysql_query(top_tables_query)
        if ok:
            top_tables = []
            for line in lines:
                parts = self._parse_tab_row(line)
                if len(parts) < 4:
                    continue
                top_tables.append(
                    {
                        "table": parts[0],
                        "size_mb": safe_float(parts[1]),
                        "rows": safe_int(parts[2]),
                        "engine": parts[3],
                    }
                )
            result["largest_tables"] = top_tables

        # MySQL runtime settings and cache hit ratio
        mysql_vars_query = (
            "SHOW VARIABLES WHERE Variable_name IN "
            "('slow_query_log','long_query_time','innodb_buffer_pool_size');"
        )
        ok, lines, _ = self.env.run_mysql_query(mysql_vars_query)
        runtime = {}
        if ok:
            for line in lines:
                parts = self._parse_tab_row(line)
                if len(parts) < 2:
                    continue
                runtime[parts[0]] = parts[1]

        mysql_status_query = (
            "SHOW GLOBAL STATUS WHERE Variable_name IN "
            "('Innodb_buffer_pool_reads','Innodb_buffer_pool_read_requests');"
        )
        ok, lines, _ = self.env.run_mysql_query(mysql_status_query)
        status_map = {}
        if ok:
            for line in lines:
                parts = self._parse_tab_row(line)
                if len(parts) < 2:
                    continue
                status_map[parts[0]] = parts[1]

        read_requests = safe_float(status_map.get("Innodb_buffer_pool_read_requests", "0") or "0") or 0
        reads = safe_float(status_map.get("Innodb_buffer_pool_reads", "0") or "0") or 0
        hit_ratio = None
        if read_requests > 0:
            hit_ratio = round((1 - (reads / read_requests)) * 100, 4)

        runtime["buffer_pool_reads"] = reads
        runtime["buffer_pool_read_requests"] = read_requests
        runtime["buffer_pool_hit_ratio_percent"] = hit_ratio
        result["mysql_runtime"] = runtime

        # Tables without PK: often a red flag for update/delete scans.
        no_pk_query = (
            "SELECT COUNT(*) FROM information_schema.TABLES t "
            "LEFT JOIN information_schema.KEY_COLUMN_USAGE k "
            "ON t.TABLE_SCHEMA = k.TABLE_SCHEMA "
            "AND t.TABLE_NAME = k.TABLE_NAME "
            "AND k.CONSTRAINT_NAME = 'PRIMARY' "
            "WHERE t.TABLE_SCHEMA = DATABASE() "
            "AND t.TABLE_TYPE='BASE TABLE' "
            "AND k.CONSTRAINT_NAME IS NULL;"
        )
        ok, lines, _ = self.env.run_mysql_query(no_pk_query)
        tables_without_primary = safe_int(lines[0]) if ok and lines else None

        # Verify expected indexes on key Magento tables.
        key_indexes = [
            ("indexer_state", "PRIMARY"),
            ("mview_state", "PRIMARY"),
            ("catalog_product_entity", "PRIMARY"),
            ("catalog_product_entity", "SKU"),
            ("customer_entity", "PRIMARY"),
            ("sales_order", "PRIMARY"),
            ("quote", "PRIMARY"),
        ]
        missing_indexes = []
        checked_indexes = []

        for table, index_name in key_indexes:
            full_table = f"{table_prefix}{table}"
            table_exists_query = (
                "SELECT COUNT(*) FROM information_schema.TABLES "
                f"WHERE table_schema = DATABASE() AND table_name = '{full_table}';"
            )
            table_ok, table_lines, _ = self.env.run_mysql_query(table_exists_query)
            table_exists = bool(table_ok and table_lines and safe_int(table_lines[0]) == 1)
            if not table_exists:
                continue

            index_exists_query = (
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                f"WHERE table_schema = DATABASE() "
                f"AND table_name = '{full_table}' "
                f"AND index_name = '{index_name}';"
            )
            idx_ok, idx_lines, _ = self.env.run_mysql_query(index_exists_query)
            exists = bool(idx_ok and idx_lines and (safe_int(idx_lines[0]) or 0) > 0)
            checked_indexes.append(
                {"table": full_table, "index": index_name, "exists": exists}
            )
            if not exists:
                missing_indexes.append({"table": full_table, "index": index_name})

        result["index_health"] = {
            "tables_without_primary_key": tables_without_primary,
            "checked_indexes": checked_indexes,
            "missing_indexes": missing_indexes,
        }

        # Common hotspots in Magento instances
        hotspots_query = (
            "SELECT table_name, table_rows FROM information_schema.TABLES "
            "WHERE table_schema = DATABASE() "
            "AND table_name IN ("
            "'quote','quote_item','customer_visitor','report_viewed_product_index',"
            "'sales_order_grid','catalogsearch_fulltext_scope1'"
            ") ORDER BY table_rows DESC;"
        )
        ok, lines, _ = self.env.run_mysql_query(hotspots_query)
        hotspots = []
        if ok:
            for line in lines:
                parts = self._parse_tab_row(line)
                if len(parts) < 2:
                    continue
                hotspots.append({"table": parts[0], "rows": safe_int(parts[1])})
        result["table_hotspots"] = hotspots

        # Log/report-heavy tables that commonly bloat and impact query latency.
        log_tables_query = (
            "SELECT table_name, "
            "ROUND((data_length + index_length) / 1024 / 1024, 2) AS size_mb, "
            "table_rows "
            "FROM information_schema.TABLES "
            "WHERE table_schema = DATABASE() "
            "AND ("
            "table_name LIKE '%\\_log%' ESCAPE '\\' "
            "OR table_name LIKE 'log\\_%' ESCAPE '\\' "
            "OR table_name LIKE 'report\\_%' ESCAPE '\\' "
            "OR table_name LIKE '%\\_report%' ESCAPE '\\' "
            "OR table_name LIKE '%\\_visitor%' ESCAPE '\\' "
            "OR table_name LIKE '%cron_schedule%' "
            "OR table_name LIKE '%search_query%'"
            ") "
            "ORDER BY (data_length + index_length) DESC "
            "LIMIT 25;"
        )
        ok, lines, _ = self.env.run_mysql_query(log_tables_query)
        log_tables = []
        if ok:
            for line in lines:
                parts = self._parse_tab_row(line)
                if len(parts) < 3:
                    continue
                log_tables.append(
                    {
                        "table": parts[0],
                        "size_mb": safe_float(parts[1]),
                        "rows": safe_int(parts[2]),
                    }
                )
        result["log_related_tables"] = log_tables

        # Fragmented tables (data_free overhead).
        frag_query = (
            "SELECT table_name, "
            "ROUND((data_free / NULLIF((data_length + index_length), 0)) * 100, 2) AS overhead_pct, "
            "ROUND((data_length + index_length) / 1024 / 1024, 2) AS size_mb "
            "FROM information_schema.TABLES "
            "WHERE table_schema = DATABASE() "
            "AND (data_length + index_length) > 0 "
            "ORDER BY overhead_pct DESC "
            "LIMIT 20;"
        )
        ok, lines, _ = self.env.run_mysql_query(frag_query)
        fragmented = []
        if ok:
            for line in lines:
                parts = self._parse_tab_row(line)
                if len(parts) < 3:
                    continue
                fragmented.append(
                    {
                        "table": parts[0],
                        "overhead_percent": safe_float(parts[1]),
                        "size_mb": safe_float(parts[2]),
                    }
                )
        result["fragmentation"] = fragmented

        # Columns ending in _id without FK references (heuristic).
        fk_query = (
            "SELECT c.table_name, c.column_name "
            "FROM information_schema.COLUMNS c "
            "LEFT JOIN information_schema.KEY_COLUMN_USAGE k "
            "ON c.table_schema = k.table_schema "
            "AND c.table_name = k.table_name "
            "AND c.column_name = k.column_name "
            "AND k.referenced_table_name IS NOT NULL "
            "WHERE c.table_schema = DATABASE() "
            "AND c.column_name LIKE '%\\_id' ESCAPE '\\' "
            "AND c.column_name NOT IN ('entity_id', 'row_id') "
            "AND k.referenced_table_name IS NULL "
            "LIMIT 100;"
        )
        ok, lines, _ = self.env.run_mysql_query(fk_query)
        missing_fk_columns = []
        if ok:
            for line in lines:
                parts = self._parse_tab_row(line)
                if len(parts) < 2:
                    continue
                missing_fk_columns.append({"table": parts[0], "column": parts[1]})
        result["foreign_keys"] = {
            "missing_fk_columns_sample": missing_fk_columns,
            "missing_fk_count_sample": len(missing_fk_columns),
        }

        # Orphaned data examples.
        orphan_queries = {
            "products_without_website": (
                f"SELECT COUNT(*) FROM {table_prefix}catalog_product_entity p "
                f"LEFT JOIN {table_prefix}catalog_product_website pw ON p.entity_id = pw.product_id "
                "WHERE pw.product_id IS NULL;"
            ),
            "products_without_category": (
                f"SELECT COUNT(*) FROM {table_prefix}catalog_product_entity p "
                f"LEFT JOIN {table_prefix}catalog_category_product cp ON p.entity_id = cp.product_id "
                "WHERE cp.product_id IS NULL;"
            ),
        }
        orphaned = {}
        for key, sql in orphan_queries.items():
            ok, lines, _ = self.env.run_mysql_query(sql)
            if ok and lines:
                orphaned[key] = safe_int(lines[0])
        result["orphaned_data"] = orphaned

        # EAV bloat indicators.
        bloat = {}
        eav_metric_queries = {
            "product_attributes_user_defined": (
                f"SELECT COUNT(*) FROM {table_prefix}eav_attribute ea "
                f"JOIN {table_prefix}eav_entity_type et ON et.entity_type_id=ea.entity_type_id "
                "WHERE et.entity_type_code='catalog_product' AND ea.is_user_defined=1;"
            ),
            "product_text_attributes": (
                f"SELECT COUNT(*) FROM {table_prefix}eav_attribute ea "
                f"JOIN {table_prefix}eav_entity_type et ON et.entity_type_id=ea.entity_type_id "
                "WHERE et.entity_type_code='catalog_product' AND ea.backend_type='text';"
            ),
            "empty_varchar_values": (
                f"SELECT COUNT(*) FROM {table_prefix}catalog_product_entity_varchar "
                "WHERE value='' OR value IS NULL;"
            ),
            "empty_text_values": (
                f"SELECT COUNT(*) FROM {table_prefix}catalog_product_entity_text "
                "WHERE value='' OR value IS NULL;"
            ),
        }
        for key, sql in eav_metric_queries.items():
            table_name = sql.split("FROM ")[1].split()[0]
            exists_ok, exists_lines, _ = self.env.run_mysql_query(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                f"WHERE table_schema = DATABASE() AND table_name = '{table_name}';"
            )
            exists = bool(exists_ok and exists_lines and safe_int(exists_lines[0]) == 1)
            if not exists:
                continue
            ok, lines, _ = self.env.run_mysql_query(sql)
            if ok and lines:
                bloat[key] = safe_int(lines[0])
        result["eav_bloat"] = bloat

        # Session cleanup metrics.
        session_table = f"{table_prefix}session"
        exists_ok, exists_lines, _ = self.env.run_mysql_query(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE table_schema = DATABASE() AND table_name = '{session_table}';"
        )
        session_exists = bool(exists_ok and exists_lines and safe_int(exists_lines[0]) == 1)
        session_metrics = {"table_exists": session_exists}
        if session_exists:
            ok, lines, _ = self.env.run_mysql_query(f"SELECT COUNT(*) FROM {session_table};")
            if ok and lines:
                session_metrics["total_sessions"] = safe_int(lines[0])
            ok, lines, _ = self.env.run_mysql_query(
                f"SELECT COUNT(*) FROM {session_table} WHERE session_expires < UNIX_TIMESTAMP();"
            )
            if ok and lines:
                session_metrics["expired_sessions"] = safe_int(lines[0])
        result["session_cleanup"] = session_metrics

        # Quote and order retention metrics.
        retention = {}
        retention_queries = {
            "active_quotes_older_than_30d": (
                f"SELECT COUNT(*) FROM {table_prefix}quote "
                "WHERE is_active = 1 AND updated_at < (NOW() - INTERVAL 30 DAY);"
            ),
            "pending_orders_older_than_30d": (
                f"SELECT COUNT(*) FROM {table_prefix}sales_order "
                "WHERE status IN ('pending_payment', 'pending') "
                "AND created_at < (NOW() - INTERVAL 30 DAY);"
            ),
        }
        for key, sql in retention_queries.items():
            table_name = sql.split("FROM ")[1].split()[0]
            exists_ok, exists_lines, _ = self.env.run_mysql_query(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                f"WHERE table_schema = DATABASE() AND table_name = '{table_name}';"
            )
            exists = bool(exists_ok and exists_lines and safe_int(exists_lines[0]) == 1)
            if not exists:
                continue
            ok, lines, _ = self.env.run_mysql_query(sql)
            if ok and lines:
                retention[key] = safe_int(lines[0])
        result["retention"] = retention

        # EXPLAIN-based warnings on common queries.
        explain_queries = [
            (
                "Recent Orders Grid",
                f"SELECT entity_id FROM {table_prefix}sales_order_grid ORDER BY entity_id DESC LIMIT 1000",
            ),
            (
                "Product URL Rewrite",
                f"SELECT request_path FROM {table_prefix}url_rewrite "
                "WHERE entity_type='product' AND redirect_type=0 LIMIT 5000",
            ),
            (
                "Quote Active Scan",
                f"SELECT entity_id FROM {table_prefix}quote WHERE is_active=1 LIMIT 10000",
            ),
        ]
        explain_warnings = []
        for query_name, sql in explain_queries:
            table_name = sql.split("FROM ")[1].split()[0]
            exists_ok, exists_lines, _ = self.env.run_mysql_query(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                f"WHERE table_schema = DATABASE() AND table_name = '{table_name}';"
            )
            exists = bool(exists_ok and exists_lines and safe_int(exists_lines[0]) == 1)
            if not exists:
                continue

            ok, lines, _ = self.env.run_mysql_query(f"EXPLAIN FORMAT=JSON {sql}")
            if not ok or not lines:
                continue
            plan = lines[0]
            if '"access_type": "ALL"' in plan:
                explain_warnings.append({"query": query_name, "issue": "full_table_scan_detected"})
            if '"using_filesort": true' in plan:
                explain_warnings.append({"query": query_name, "issue": "filesort_detected"})
            if '"using_temporary_table": true' in plan:
                explain_warnings.append({"query": query_name, "issue": "temporary_table_detected"})
        result["query_explain_warnings"] = explain_warnings

        size_mb = result.get("database_size", {}).get("size_mb")
        if missing_indexes:
            result["status"] = "warning"
        if tables_without_primary and tables_without_primary > 0:
            result["status"] = "warning"
        if hit_ratio is not None and hit_ratio < 99.0:
            result["status"] = "warning"
        if size_mb is not None and size_mb > 10240:
            result["status"] = "warning"
        if explain_warnings:
            result["status"] = "warning"
        if orphaned.get("products_without_website", 0) > 0:
            result["status"] = "warning"
        if any((item.get("size_mb") or 0) > 500 for item in log_tables):
            result["status"] = "warning"

        status = result["status"]
        status_color = (
            Colors.GREEN if status == "good" else Colors.ORANGE if status == "warning" else Colors.RED
        )
        print(
            f"{status_color}DB size: {result.get('database_size', {}).get('size_mb', 'n/a')} MB | "
            f"Buffer hit ratio: {hit_ratio if hit_ratio is not None else 'n/a'}% | "
            f"Missing indexes: {len(missing_indexes)}{Colors.RESET}"
        )
        if missing_indexes:
            print(f"{Colors.ORANGE}Missing expected indexes:{Colors.RESET}")
            for item in missing_indexes[:10]:
                print(f"  - {item.get('table')}.{item.get('index')}")
        if explain_warnings:
            print(f"{Colors.ORANGE}EXPLAIN warnings detected:{Colors.RESET}")
            for warn in explain_warnings[:10]:
                print(f"  - {warn.get('query')}: {warn.get('issue')}")
        if log_tables:
            print(f"{Colors.CYAN}Top log/report-related tables:{Colors.RESET}")
            for item in log_tables[:5]:
                print(
                    f"  - {item.get('table')}: {item.get('size_mb')}MB, "
                    f"rows={item.get('rows')}"
                )

        return result
