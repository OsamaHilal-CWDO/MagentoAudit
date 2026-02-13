"""Database performance and index coverage checks for Magento."""

from __future__ import annotations

from typing import Dict, List

from ..base import Colors, MagentoEnvironment
from ..utils import safe_float, safe_int


class DatabaseAuditModule:
    """Review DB sizing, MySQL settings, and key index coverage."""

    name = "database"

    def __init__(self, env: MagentoEnvironment):
        self.env = env

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

        size_mb = result.get("database_size", {}).get("size_mb")
        if missing_indexes:
            result["status"] = "warning"
        if tables_without_primary and tables_without_primary > 0:
            result["status"] = "warning"
        if hit_ratio is not None and hit_ratio < 99.0:
            result["status"] = "warning"
        if size_mb is not None and size_mb > 10240:
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

        return result
