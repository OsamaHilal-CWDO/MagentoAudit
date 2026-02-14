#!/usr/bin/env python3
"""Single-file bootstrap runner for Magento audit.

Usage:
  curl -fsSL <raw-bootstrap-url> | python3 - -- --magento-root /path/to/magento

You can also customize source repo/ref:
  python3 bootstrap.py --repo owner/repo --ref main -- --magento-root /path/to/magento

Private repos are supported with a GitHub token:
  export GITHUB_TOKEN=ghp_xxx
  python3 bootstrap.py --repo owner/private-repo --ref main -- --magento-root /path
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from typing import List, Tuple


DEFAULT_REPO = "OsamaHilal-CWDO/MagentoAudit"
DEFAULT_REF = "cursor/magento-performance-audit-7546"
DEFAULT_ENTRYPOINT = "magento_health_audit.py"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap MagentoAudit from GitHub archive and run magento_health_audit.py "
            "without cloning the repo manually."
        )
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"GitHub repo in owner/name format (default: {DEFAULT_REPO}).",
    )
    parser.add_argument(
        "--ref",
        default=DEFAULT_REF,
        help=f"Branch/tag/commit ref to download (default: {DEFAULT_REF}).",
    )
    parser.add_argument(
        "--entrypoint",
        default=DEFAULT_ENTRYPOINT,
        help=f"Entrypoint script inside repo (default: {DEFAULT_ENTRYPOINT}).",
    )
    parser.add_argument(
        "--github-token-env",
        default="GITHUB_TOKEN",
        help="Environment variable containing GitHub token for private repos.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep downloaded/extracted temp directory after execution.",
    )
    parser.add_argument(
        "--download-timeout",
        type=int,
        default=180,
        help="Archive download timeout in seconds (default: 180).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would run and exit without downloading.",
    )
    return parser


def parse_args(argv: List[str]) -> Tuple[argparse.Namespace, List[str]]:
    parser = build_parser()
    if "--" in argv:
        # Explicit mode:
        #   python3 bootstrap.py [bootstrap-opts] -- [audit-opts]
        split = argv.index("--")
        bootstrap_argv = argv[:split]
        audit_argv = argv[split + 1 :]
        args, unknown = parser.parse_known_args(bootstrap_argv)
        passthrough = unknown + audit_argv
    else:
        # Implicit mode:
        #   python3 bootstrap.py [bootstrap-opts] [audit-opts]
        args, passthrough = parser.parse_known_args(argv)
        if passthrough and passthrough[0] == "--":
            passthrough = passthrough[1:]
    return args, passthrough


def archive_url(repo: str, ref: str) -> str:
    # API tarball endpoint supports private repos when Authorization header is used.
    return f"https://api.github.com/repos/{repo}/tarball/{ref}"


def download_archive(url: str, token: str, timeout: int, target_file: str) -> None:
    headers = {"User-Agent": "MagentoAuditBootstrap/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response, open(target_file, "wb") as out:
        shutil.copyfileobj(response, out)


def extract_archive(archive_path: str, output_dir: str) -> str:
    with tarfile.open(archive_path, "r:gz") as tar:
        members = tar.getmembers()
        tar.extractall(path=output_dir)
    top_names = []
    for member in members:
        if not member.name:
            continue
        first = member.name.split("/", 1)[0]
        if first and first not in top_names:
            top_names.append(first)
    if not top_names:
        raise RuntimeError("Archive extraction failed: no top-level directory found")
    return os.path.join(output_dir, top_names[0])


def main(argv: List[str]) -> int:
    args, passthrough = parse_args(argv)
    token = os.environ.get(args.github_token_env, "").strip()
    url = archive_url(args.repo, args.ref)

    print(f"[bootstrap] repo={args.repo} ref={args.ref}", file=sys.stderr)
    print(f"[bootstrap] url={url}", file=sys.stderr)

    if args.dry_run:
        print("[bootstrap] dry-run enabled; no download executed", file=sys.stderr)
        print(f"[bootstrap] would forward args: {passthrough}", file=sys.stderr)
        return 0

    temp_root = tempfile.mkdtemp(prefix="magento_audit_bootstrap_")
    archive_file = os.path.join(temp_root, "source.tar.gz")
    project_root = ""
    try:
        print("[bootstrap] downloading archive...", file=sys.stderr)
        download_archive(url=url, token=token, timeout=args.download_timeout, target_file=archive_file)
        print("[bootstrap] extracting archive...", file=sys.stderr)
        project_root = extract_archive(archive_path=archive_file, output_dir=temp_root)

        entrypoint = os.path.join(project_root, args.entrypoint)
        if not os.path.isfile(entrypoint):
            raise RuntimeError(f"Entrypoint not found: {entrypoint}")

        command = [sys.executable, entrypoint] + passthrough
        print(f"[bootstrap] executing: {' '.join(command)}", file=sys.stderr)
        result = subprocess.run(command, cwd=project_root)
        return int(result.returncode)
    except Exception as exc:
        print(f"[bootstrap] error: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep_temp:
            print(f"[bootstrap] temp kept at: {temp_root}", file=sys.stderr)
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
