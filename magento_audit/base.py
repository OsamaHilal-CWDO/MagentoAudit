#!/usr/bin/env python3
"""Shared helpers for Magento health audits."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import base64
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


class Colors:
    """ANSI colors for terminal output."""

    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ORANGE = "\033[38;5;214m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


@dataclass
class CommandResult:
    """A normalized subprocess execution result."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class MagentoEnvironment:
    """Runtime environment helper for Magento CLI/config/database calls."""

    def __init__(
        self,
        root_path: str = ".",
        magento_bin: str = "bin/magento",
        php_bin: str = "php",
        mysql_bin: str = "mysql",
        site_url: Optional[str] = None,
        log_path: Optional[str] = None,
    ):
        self.root_path = os.path.abspath(root_path)
        self.magento_bin = magento_bin
        self.php_bin = php_bin
        self.mysql_bin = mysql_bin
        self.site_url = self._normalize_url(site_url)
        self.log_path = os.path.abspath(log_path) if log_path else os.path.join(self.root_path, "var", "log")
        self._env_cache: Optional[Dict[str, Any]] = None
        self._site_url_cache: Optional[str] = None

    @property
    def magento_bin_path(self) -> str:
        return os.path.join(self.root_path, self.magento_bin)

    @property
    def env_php_path(self) -> str:
        return os.path.join(self.root_path, "app", "etc", "env.php")

    def print_section(self, title: str) -> None:
        """Print a formatted section header."""
        print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 72}{Colors.RESET}")
        print(f"{Colors.CYAN}{Colors.BOLD}{title}{Colors.RESET}")
        print(f"{Colors.CYAN}{Colors.BOLD}{'=' * 72}{Colors.RESET}\n")

    def run_command(
        self,
        args: List[str],
        timeout: int = 60,
        env: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        """Execute a command safely and return a normalized result."""
        cmd_text = " ".join(shlex.quote(arg) for arg in args)
        try:
            result = subprocess.run(
                args,
                cwd=self.root_path,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            return CommandResult(
                command=cmd_text,
                exit_code=result.returncode,
                stdout=(result.stdout or "").strip(),
                stderr=(result.stderr or "").strip(),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=cmd_text,
                exit_code=124,
                stdout=(exc.stdout or "").strip() if exc.stdout else "",
                stderr=(exc.stderr or "").strip() if exc.stderr else "Command timed out",
                timed_out=True,
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            return CommandResult(
                command=cmd_text,
                exit_code=1,
                stdout="",
                stderr=str(exc),
            )

    def run_magento(self, command: str, timeout: int = 120) -> CommandResult:
        """Execute a Magento CLI command."""
        args = [self.magento_bin_path]
        args.extend(shlex.split(command))
        return self.run_command(args=args, timeout=timeout)

    def _normalize_url(self, url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        cleaned = url.strip()
        if not cleaned:
            return None
        parsed = urlparse(cleaned)
        if not parsed.scheme:
            cleaned = "https://" + cleaned
            parsed = urlparse(cleaned)
        if not parsed.netloc:
            return None
        return cleaned.rstrip("/") + "/"

    def resolve_site_url(self) -> Optional[str]:
        """Resolve the storefront base URL from args or Magento config."""
        if self._site_url_cache:
            return self._site_url_cache
        if self.site_url:
            self._site_url_cache = self.site_url
            return self._site_url_cache

        for path in ("web/secure/base_url", "web/unsecure/base_url"):
            result = self.run_magento(f"config:show {path}", timeout=20)
            if result.ok and result.stdout:
                resolved = self._normalize_url(result.stdout.strip())
                if resolved:
                    self._site_url_cache = resolved
                    return self._site_url_cache
        return None

    def load_env_config(self) -> Dict[str, Any]:
        """Load app/etc/env.php as a Python dict via PHP JSON encoding."""
        if self._env_cache is not None:
            return self._env_cache

        if not os.path.exists(self.env_php_path):
            self._env_cache = {}
            return self._env_cache

        escaped_path = self.env_php_path.replace("\\", "\\\\").replace('"', '\\"')
        php_code = (
            f'$config = include "{escaped_path}"; '
            "echo json_encode($config, JSON_UNESCAPED_SLASHES);"
        )

        result = self.run_command([self.php_bin, "-r", php_code], timeout=20)
        if not result.ok or not result.stdout:
            self._env_cache = {}
            return self._env_cache

        try:
            self._env_cache = json.loads(result.stdout)
        except json.JSONDecodeError:
            self._env_cache = {}
        return self._env_cache

    def get_table_prefix(self) -> str:
        """Return configured table prefix if present."""
        config = self.load_env_config()
        prefix = config.get("db", {}).get("table_prefix", "")
        return prefix or ""

    def get_db_connection(self) -> Optional[Dict[str, Any]]:
        """Return default DB connection settings from env.php."""
        config = self.load_env_config()
        db_config = config.get("db", {}).get("connection", {}).get("default")
        if not isinstance(db_config, dict):
            return None
        return db_config

    def run_mysql_query(self, query: str, timeout: int = 45) -> Tuple[bool, List[str], str]:
        """Run a SQL query using DB credentials from env.php."""
        db = self.get_db_connection()
        if not db:
            return False, [], "Database connection config not found in app/etc/env.php"

        host = str(db.get("host") or "127.0.0.1")
        user = str(db.get("username") or "")
        password = str(db.get("password") or "")
        database = str(db.get("dbname") or "")
        port = str(db.get("port") or "")
        unix_socket = str(db.get("unix_socket") or "")

        if not user or not database:
            return False, [], "Incomplete database credentials in app/etc/env.php"

        if host and ":" in host and host.count(":") == 1 and not port:
            host, port = host.split(":", 1)

        args = [
            self.mysql_bin,
            f"--user={user}",
            f"--database={database}",
            "--batch",
            "--raw",
            "--skip-column-names",
            "-e",
            query,
        ]
        if host:
            args.insert(1, f"--host={host}")
        if port:
            args.insert(3, f"--port={port}")
        if unix_socket:
            args.insert(3, f"--socket={unix_socket}")

        env = os.environ.copy()
        if password:
            env["MYSQL_PWD"] = password

        result = self.run_command(args=args, timeout=timeout, env=env)
        if result.ok:
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return True, lines, ""

        mysql_error = result.stderr or result.stdout or "Unknown MySQL execution error"

        # Fallback: run the query via PHP PDO using the same env.php credentials.
        pdo_ok, pdo_lines, pdo_error = self._run_mysql_query_via_php(query=query, timeout=timeout)
        if pdo_ok:
            return True, pdo_lines, ""

        combined_error = (
            f"MySQL CLI error: {mysql_error}; "
            f"PHP PDO fallback error: {pdo_error}"
        )
        return False, [], combined_error

    def _run_mysql_query_via_php(self, query: str, timeout: int = 45) -> Tuple[bool, List[str], str]:
        """Fallback SQL execution via PHP PDO when mysql CLI auth/path fails."""
        if not os.path.exists(self.env_php_path):
            return False, [], "env.php not found for PDO fallback"

        query_b64 = base64.b64encode(query.encode("utf-8")).decode("ascii")
        env_path = self.env_php_path.replace("\\", "\\\\").replace('"', '\\"')
        php_code = (
            f'$cfg = include "{env_path}";'
            '$db = $cfg["db"]["connection"]["default"] ?? null;'
            'if (!$db) { fwrite(STDERR, "DB config missing"); exit(2); }'
            '$host = (string)($db["host"] ?? "127.0.0.1");'
            '$port = (string)($db["port"] ?? "");'
            '$socket = (string)($db["unix_socket"] ?? "");'
            '$user = (string)($db["username"] ?? "");'
            '$pass = (string)($db["password"] ?? "");'
            '$name = (string)($db["dbname"] ?? "");'
            'if ($user === "" || $name === "") { fwrite(STDERR, "Incomplete DB credentials"); exit(2); }'
            'if ($port === "" && strpos($host, ":") !== false && substr_count($host, ":") === 1) {'
            '  $parts = explode(":", $host, 2);'
            '  $host = $parts[0];'
            '  $port = $parts[1];'
            '}'
            '$dsn = "mysql:";'
            'if ($socket !== "") { $dsn .= "unix_socket=" . $socket . ";"; }'
            'if ($host !== "") { $dsn .= "host=" . $host . ";"; }'
            'if ($port !== "") { $dsn .= "port=" . $port . ";"; }'
            '$dsn .= "dbname=" . $name . ";charset=utf8mb4";'
            '$sql = base64_decode("' + query_b64 + '");'
            'try {'
            '  $pdo = new PDO($dsn, $user, $pass, ['
            '    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,'
            '    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_NUM,'
            '  ]);'
            '  $stmt = $pdo->query($sql);'
            '  if ($stmt) {'
            '    while (($row = $stmt->fetch(PDO::FETCH_NUM)) !== false) {'
            '      $out = array_map(function ($v) {'
            '        if ($v === null) { return "NULL"; }'
            '        $s = (string)$v;'
            '        $s = str_replace(["\\t", "\\n", "\\r"], " ", $s);'
            '        return $s;'
            '      }, $row);'
            '      echo implode("\\t", $out) . PHP_EOL;'
            '    }'
            '  }'
            '} catch (Throwable $e) {'
            '  fwrite(STDERR, $e->getMessage());'
            '  exit(1);'
            '}'
        )

        result = self.run_command([self.php_bin, "-r", php_code], timeout=timeout)
        if not result.ok:
            error = result.stderr or result.stdout or "PDO fallback failed"
            return False, [], error
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return True, lines, ""

    def validate_magento_installation(self) -> Tuple[bool, str]:
        """Basic Magento root sanity checks."""
        if not os.path.exists(self.magento_bin_path):
            return False, f"Magento binary not found at {self.magento_bin_path}"
        if not os.path.exists(self.env_php_path):
            return False, f"Magento env config not found at {self.env_php_path}"
        return True, ""
