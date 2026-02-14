"""Recommendation engine for Magento audit findings."""

from __future__ import annotations

from typing import Dict, List


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _add(recs: List[Dict], severity: str, module: str, title: str, detail: str) -> None:
    recs.append(
        {
            "severity": severity,
            "module": module,
            "title": title,
            "detail": detail,
        }
    )


def generate_recommendations(report: Dict) -> List[Dict]:
    """Generate prioritized recommendations from module outputs."""
    modules = report.get("modules", {})
    recs: List[Dict] = []

    frontend = modules.get("frontend", {})
    ttfb = frontend.get("ttfb", {})
    if ttfb.get("status") == "critical":
        _add(
            recs,
            "critical",
            "frontend",
            "High TTFB detected",
            f"Average TTFB is {ttfb.get('average_ms')}ms. Investigate full-page cache, Redis, and upstream latency.",
        )

    backend = modules.get("backend", {})
    slowest_benchmarks = backend.get("slowest_benchmark_queries", [])
    if slowest_benchmarks and (slowest_benchmarks[0].get("avg_ms") or 0) > 300:
        top = slowest_benchmarks[0]
        _add(
            recs,
            "high",
            "backend",
            f"Slow benchmark query hotspot: {top.get('name')}",
            f"Average {top.get('avg_ms')}ms. Focus index tuning and EXPLAIN plan validation for this query path.",
        )
    for query in backend.get("query_benchmarks", []):
        if query.get("status") == "critical":
            _add(
                recs,
                "critical",
                "backend",
                f"Slow query benchmark: {query.get('name')}",
                f"Average {query.get('avg_ms')}ms. Review indexes and query plan for this workload.",
            )
    slow_entries = backend.get("slow_query_log", {}).get("timed_entries", 0)
    if slow_entries and slow_entries > 100:
        _add(
            recs,
            "high",
            "backend",
            "Elevated MySQL slow query volume",
            f"Detected {slow_entries} timed slow-log entries in analysis window.",
        )
    if backend.get("query_plan_warnings"):
        _add(
            recs,
            "high",
            "backend",
            "Benchmark query plan warnings",
            f"{len(backend.get('query_plan_warnings', []))} warnings detected (full scans/filesort/temp tables).",
        )

    db = modules.get("database", {})
    missing_indexes = db.get("index_health", {}).get("missing_indexes", [])
    if missing_indexes:
        _add(
            recs,
            "high",
            "database",
            "Missing expected indexes",
            f"{len(missing_indexes)} expected indexes are missing on core tables.",
        )
    if db.get("query_explain_warnings"):
        _add(
            recs,
            "high",
            "database",
            "EXPLAIN plan warnings detected",
            f"{len(db.get('query_explain_warnings', []))} warnings (full scans/filesort/temp tables) were detected.",
        )
    heavy_log_tables = [
        item
        for item in db.get("log_related_tables", [])
        if (item.get("size_mb") or 0) > 250 or (item.get("rows") or 0) > 1_000_000
    ]
    if heavy_log_tables:
        top = heavy_log_tables[0]
        _add(
            recs,
            "medium",
            "database",
            "Large log/report tables detected",
            f"Table {top.get('table')} is {top.get('size_mb')}MB with {top.get('rows')} rows. Consider retention/cleanup policy.",
        )

    indexers = modules.get("indexers", {})
    if indexers.get("invalid_indexers"):
        _add(
            recs,
            "critical",
            "indexers",
            "Invalid indexers detected",
            f"{len(indexers.get('invalid_indexers', []))} indexers require reindexing.",
        )

    cache = modules.get("cache", {})
    if cache.get("disabled_critical_cache_types"):
        _add(
            recs,
            "critical",
            "cache",
            "Critical cache types are disabled",
            ", ".join(cache.get("disabled_critical_cache_types", [])),
        )
    elif cache.get("cache_backend", {}).get("default", {}).get("type") == "filesystem":
        _add(
            recs,
            "medium",
            "cache",
            "Filesystem cache backend in use",
            "Consider Redis for lower latency and better concurrency under load.",
        )
    if cache.get("cache_backend", {}).get("page_cache", {}).get("type") == "filesystem":
        _add(
            recs,
            "medium",
            "cache",
            "Page cache is filesystem-backed",
            "Consider Redis or Varnish-backed full-page cache for faster cache lookup and better scale.",
        )

    sessions = modules.get("sessions", {})
    handler = sessions.get("save_handler")
    if handler in {"db", "database", "files"}:
        _add(
            recs,
            "medium",
            "sessions",
            "Session backend could be optimized",
            f"Current session handler is '{handler}'. Redis is preferred for scale.",
        )

    logs = modules.get("logs", {})
    http_500 = logs.get("access_logs", {}).get("http_errors", {}).get("500", 0)
    if http_500 and http_500 > 20:
        top_500_urls = logs.get("access_logs", {}).get("top_500_urls", [])
        top_url = top_500_urls[0].get("path") if top_500_urls else "unknown"
        _add(
            recs,
            "high",
            "logs",
            "Frequent HTTP 500 responses",
            f"{http_500} 500 responses found. Top failing URL: {top_url}",
        )
    top_500_hours = logs.get("access_logs", {}).get("top_500_hours", [])
    if top_500_hours:
        peak = top_500_hours[0]
        _add(
            recs,
            "medium",
            "logs",
            "Concentrated 500 error window detected",
            f"Peak window {peak.get('hour')} with {peak.get('count')} HTTP 500 errors.",
        )

    profiler = modules.get("extension_profiler", {})
    top_extensions = profiler.get("top_extensions", [])
    if top_extensions and top_extensions[0].get("impact_score", 0) > 150:
        top = top_extensions[0]
        _add(
            recs,
            "high",
            "extension_profiler",
            f"High extension complexity score: {top.get('module')}",
            f"Impact score {top.get('impact_score')} with observers/plugins/preferences footprint.",
        )

    hotspots = modules.get("hotspots", {})
    if hotspots.get("issues"):
        _add(
            recs,
            "medium",
            "hotspots",
            "Magento data-shape hotspots detected",
            ", ".join(hotspots.get("issues", [])),
        )

    production = modules.get("production", {})
    if production.get("deploy_mode") == "developer":
        _add(
            recs,
            "critical",
            "production",
            "Developer mode enabled in production audit",
            "Switch to production mode and ensure generated code/static assets are up to date.",
        )

    security = modules.get("security", {})
    if security.get("file_permissions", {}).get("env_php_world_writable"):
        _add(
            recs,
            "critical",
            "security",
            "env.php is world-writable",
            "Restrict file permissions immediately to prevent secret compromise.",
        )
    if not security.get("two_factor_auth", {}).get("module_enabled", True):
        _add(
            recs,
            "high",
            "security",
            "Two-factor authentication is disabled",
            "Enable Magento_TwoFactorAuth for admin accounts.",
        )

    recs.sort(key=lambda row: (SEVERITY_ORDER.get(row["severity"], 99), row["module"], row["title"]))
    return recs
