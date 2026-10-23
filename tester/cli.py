from __future__ import annotations

import argparse
import asyncio
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from tester.agent_runner import run_flow
from tester.report import write_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tester")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="Test a plain-English website flow")
    run_parser.add_argument("--url", required=True)
    run_parser.add_argument("--username", default=os.getenv("SITE_USERNAME"))
    run_parser.add_argument("--password", default=os.getenv("SITE_PASSWORD"))
    run_parser.add_argument("--flow", required=True)
    return parser


def report_directory() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("reports") / f"{stamp}-{secrets.token_hex(2)}"


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.username or not args.password:
        parser.error("username and password are required via flags or SITE_USERNAME and SITE_PASSWORD")
    api_key = os.getenv("CURSOR_API_KEY")
    if not api_key:
        parser.error("CURSOR_API_KEY is required")

    directory = report_directory()
    report = asyncio.run(
        run_flow(
            url=args.url,
            username=args.username,
            password=args.password,
            flow=args.flow,
            api_key=api_key,
            report_dir=directory,
        )
    )
    write_report(report, directory, secrets=[args.password, args.username])
    status = "pass" if report.passed else "fail"
    print(f"{status}: {directory / 'index.html'}")
    return 0 if report.passed else 1
