"""Indexer and index-state checks for Magento."""

from __future__ import annotations

from typing import Dict, List

from ..base import Colors, MagentoEnvironment
from ..utils import parse_magento_table, safe_int


class IndexersAuditModule:
    """Audit indexer validity, modes, and mview backlog state."""

    name = "indexers"

    def __init__(self, env: MagentoEnvironment, runtime_options: Dict | None = None):
        self.env = env
        self.runtime_options = runtime_options or {}

    def _parse_mode_lines(self, output: str) -> List[Dict[str, str]]:
        rows = parse_magento_table(output)
        if rows:
            return rows

        parsed = []
        for line in output.splitlines():
            if ":" not in line:
                continue
            left, right = line.split(":", 1)
            parsed.append({"indexer": left.strip(), "mode": right.strip()})
        return parsed

    def run(self) -> Dict:
        print(f"{Colors.CYAN}Reviewing Magento indexers and index states...{Colors.RESET}")

        status_result = self.env.run_magento("indexer:status", timeout=120)
        mode_result = self.env.run_magento("indexer:show-mode", timeout=120)

        if not status_result.ok:
            error = status_result.stderr or status_result.stdout or "indexer:status failed"
            print(f"{Colors.RED}Unable to read indexer status: {error}{Colors.RESET}")
            return {"status": "error", "error": error}

        status_rows = parse_magento_table(status_result.stdout)
        mode_rows = self._parse_mode_lines(mode_result.stdout if mode_result.ok else "")

        invalid = []
        processing = []
        healthy = []
        scheduled = []
        realtime = []

        for row in status_rows:
            blob = " ".join(row.values()).lower()
            if "reindex required" in blob or "invalid" in blob:
                invalid.append(row)
            elif "processing" in blob:
                processing.append(row)
            else:
                healthy.append(row)

        for row in mode_rows:
            mode_blob = " ".join(row.values()).lower()
            if "schedule" in mode_blob:
                scheduled.append(row)
            elif "realtime" in mode_blob or "real time" in mode_blob:
                realtime.append(row)

        result: Dict = {
            "status": "good",
            "indexer_status_rows": status_rows,
            "indexer_mode_rows": mode_rows,
            "healthy_indexers": len(healthy),
            "invalid_indexers": invalid,
            "processing_indexers": processing,
            "scheduled_mode_indexers": scheduled,
            "realtime_mode_indexers": realtime,
            "mview_state": [],
            "indexer_state": [],
            "mview_backlog": [],
        }

        # Pull mview/indexer DB state for deeper visibility.
        ok, lines, _ = self.env.run_mysql_query(
            "SELECT view_id, mode, status, version_id, updated FROM mview_state ORDER BY view_id;"
        )
        mview_rows = []
        if ok:
            for line in lines:
                parts = [part.strip() for part in line.split("\t")]
                if len(parts) < 5:
                    continue
                mview_rows.append(
                    {
                        "view_id": parts[0],
                        "mode": parts[1],
                        "status": parts[2],
                        "version_id": safe_int(parts[3]),
                        "updated": parts[4],
                    }
                )
        result["mview_state"] = mview_rows

        ok, lines, _ = self.env.run_mysql_query(
            "SELECT indexer_id, status, updated FROM indexer_state ORDER BY indexer_id;"
        )
        state_rows = []
        if ok:
            for line in lines:
                parts = [part.strip() for part in line.split("\t")]
                if len(parts) < 3:
                    continue
                state_rows.append(
                    {"indexer_id": parts[0], "status": parts[1], "updated": parts[2]}
                )
        result["indexer_state"] = state_rows

        # Estimate changelog backlog by comparing mview version_id vs *_cl max(version_id).
        table_prefix = self.env.get_table_prefix()
        backlog_rows = []
        for row in mview_rows:
            view_id = row.get("view_id")
            if not view_id:
                continue
            changelog_table = f"{table_prefix}{view_id}_cl"

            exists_ok, exists_lines, _ = self.env.run_mysql_query(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                f"WHERE table_schema = DATABASE() AND table_name = '{changelog_table}';"
            )
            exists = bool(exists_ok and exists_lines and safe_int(exists_lines[0]) == 1)
            if not exists:
                continue

            max_ok, max_lines, _ = self.env.run_mysql_query(
                f"SELECT MAX(version_id) FROM {changelog_table};"
            )
            if not max_ok or not max_lines:
                continue

            max_version = safe_int(max_lines[0]) or 0
            current_version = row.get("version_id") or 0
            backlog = max(0, max_version - current_version)
            backlog_rows.append(
                {
                    "view_id": view_id,
                    "changelog_table": changelog_table,
                    "current_version": current_version,
                    "max_changelog_version": max_version,
                    "backlog_rows": backlog,
                }
            )
        result["mview_backlog"] = sorted(backlog_rows, key=lambda x: x["backlog_rows"], reverse=True)

        if invalid:
            result["status"] = "critical"
        elif processing or any(item.get("backlog_rows", 0) > 10000 for item in backlog_rows):
            result["status"] = "warning"

        status_color = (
            Colors.GREEN
            if result["status"] == "good"
            else Colors.ORANGE
            if result["status"] == "warning"
            else Colors.RED
        )
        print(
            f"{status_color}Healthy indexers: {len(healthy)} | "
            f"Invalid: {len(invalid)} | Processing: {len(processing)}{Colors.RESET}"
        )
        if backlog_rows:
            top_backlog = backlog_rows[0]
            print(
                f"{Colors.CYAN}Largest changelog backlog: {top_backlog['view_id']} = "
                f"{top_backlog['backlog_rows']} rows{Colors.RESET}"
            )

        return result
