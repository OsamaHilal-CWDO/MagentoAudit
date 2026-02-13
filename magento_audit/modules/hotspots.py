"""Magento-specific performance hotspot detection."""

from __future__ import annotations

from typing import Dict, Optional

from ..base import Colors, MagentoEnvironment
from ..utils import safe_float, safe_int


class MagentoHotspotsModule:
    """Detect common data-shape hotspots that degrade Magento performance."""

    name = "hotspots"

    def __init__(self, env: MagentoEnvironment, runtime_options: Optional[Dict] = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def _table_exists(self, table: str) -> bool:
        ok, lines, _ = self.env.run_mysql_query(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE table_schema = DATABASE() AND table_name='{table}';"
        )
        return bool(ok and lines and (safe_int(lines[0]) or 0) == 1)

    def _scalar_query(self, sql: str):
        ok, lines, _ = self.env.run_mysql_query(sql)
        if not ok or not lines:
            return None
        return lines[0]

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Checking Magento-specific performance hotspots...{Colors.RESET}")
        prefix = self.env.get_table_prefix()
        result = {"status": "good"}

        product_attr_count = self._scalar_query(
            f"SELECT COUNT(*) FROM {prefix}eav_attribute ea "
            f"JOIN {prefix}eav_entity_type et ON et.entity_type_id=ea.entity_type_id "
            "WHERE et.entity_type_code='catalog_product';"
        )
        result["product_attribute_count"] = safe_int(product_attr_count) if product_attr_count else None

        configurable_max_children = None
        super_link = f"{prefix}catalog_product_super_link"
        if self._table_exists(super_link):
            value = self._scalar_query(
                f"SELECT MAX(child_count) FROM ("
                f"SELECT parent_id, COUNT(*) AS child_count FROM {super_link} GROUP BY parent_id"
                ") t;"
            )
            configurable_max_children = safe_int(value) if value else None
        result["configurable_max_children"] = configurable_max_children

        category_depth = self._scalar_query(f"SELECT MAX(level) FROM {prefix}catalog_category_entity;")
        result["max_category_depth"] = safe_int(category_depth) if category_depth else None

        rewrite_count = None
        rewrite_table = f"{prefix}url_rewrite"
        if self._table_exists(rewrite_table):
            value = self._scalar_query(f"SELECT COUNT(*) FROM {rewrite_table};")
            rewrite_count = safe_int(value) if value else None
        result["url_rewrite_count"] = rewrite_count

        synonym_count = None
        synonyms_table = f"{prefix}search_synonyms"
        if self._table_exists(synonyms_table):
            value = self._scalar_query(f"SELECT COUNT(*) FROM {synonyms_table};")
            synonym_count = safe_int(value) if value else None
        result["search_synonym_count"] = synonym_count

        stopwords_count = None
        stopwords_table = f"{prefix}search_stopwords"
        if self._table_exists(stopwords_table):
            value = self._scalar_query(f"SELECT COUNT(*) FROM {stopwords_table};")
            stopwords_count = safe_int(value) if value else None
        result["search_stopword_count"] = stopwords_count

        catalog_rule_table = f"{prefix}catalogrule"
        catalog_rules = None
        avg_rule_size = None
        if self._table_exists(catalog_rule_table):
            value = self._scalar_query(f"SELECT COUNT(*) FROM {catalog_rule_table};")
            catalog_rules = safe_int(value) if value else None
            size_value = self._scalar_query(
                f"SELECT ROUND(AVG(CHAR_LENGTH(CONCAT(IFNULL(conditions_serialized,''), IFNULL(actions_serialized,'')))),2) "
                f"FROM {catalog_rule_table};"
            )
            avg_rule_size = safe_float(size_value) if size_value else None
        result["catalog_price_rules"] = {
            "count": catalog_rules,
            "average_serialized_size_chars": avg_rule_size,
        }

        customer_segment_count = None
        segment_tables = [f"{prefix}magento_customersegment_segment", f"{prefix}customersegment_segment"]
        for table in segment_tables:
            if self._table_exists(table):
                value = self._scalar_query(f"SELECT COUNT(*) FROM {table};")
                customer_segment_count = safe_int(value) if value else None
                break
        result["customer_segment_count"] = customer_segment_count

        issues = []
        if result.get("product_attribute_count") and result["product_attribute_count"] > 500:
            issues.append("high_product_attribute_count")
        if configurable_max_children and configurable_max_children > 100:
            issues.append("large_configurable_children")
        if result.get("max_category_depth") and result["max_category_depth"] > 4:
            issues.append("deep_category_tree")
        if rewrite_count and rewrite_count > 500000:
            issues.append("large_url_rewrite_table")
        if catalog_rules and catalog_rules > 100:
            issues.append("high_catalog_rule_count")

        if issues:
            result["status"] = "warning"
        result["issues"] = issues

        color = Colors.GREEN if result["status"] == "good" else Colors.ORANGE
        print(
            f"{color}Attributes: {result.get('product_attribute_count')} | "
            f"Max variants: {configurable_max_children} | "
            f"URL rewrites: {rewrite_count}{Colors.RESET}"
        )
        return result
