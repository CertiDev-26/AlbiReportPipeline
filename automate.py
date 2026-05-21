#!/usr/bin/env python3
"""Refresh saved Albi project data, then generate the HTML report."""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import report
import search


BASE_DIR = Path(__file__).resolve().parent
SAVE_DIR = BASE_DIR / "saved"
LOG_DIR  = BASE_DIR / "logs"

# Load email.py without shadowing stdlib's email package
_email_spec = importlib.util.spec_from_file_location("albi_email", BASE_DIR / "mailer.py")
_email_mod = importlib.util.module_from_spec(_email_spec)
_email_spec.loader.exec_module(_email_mod)


class _Tee:
    """Write to multiple streams at once (e.g. stdout + log file)."""
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()


def _setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / (datetime.now().strftime("%m%d%y") + ".log")
    log_file = open(log_path, "a", encoding="utf-8")
    header = f"\n{'='*60}\nRun started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*60}\n"
    log_file.write(header)
    log_file.flush()
    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)
    return log_file


def _safe_dir_name(value):
    name = str(value or "unnamed").strip() or "unnamed"
    name = name.replace(os.sep, "_")
    if os.altsep:
        name = name.replace(os.altsep, "_")
    return name


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, path)


def refresh_saved_projects(fetch_files=True):
    stats = {
        "companies": 0,
        "projects": 0,
        "new_projects": 0,
        "eligible_projects": 0,
        "file_manifests": 0,
    }

    for company_name, api_key in search.COMPANIES:
        if not api_key:
            print(f"Skipping {company_name}: missing API key", flush=True)
            continue

        stats["companies"] += 1
        print(f"Fetching {company_name}...", flush=True)
        projects = search.get_all_projects(api_key)
        print(f"  {len(projects)} project(s)", flush=True)
        contacts = []
        if report._needs_referrer_company_enrichment(projects):
            try:
                contacts = search.get_all_contacts(api_key)
            except Exception as exc:
                print(f"  WARNING: contacts lookup: {exc}", flush=True)
        enriched = report.enrich_referrer_companies(projects, contacts=contacts)
        if enriched:
            print(f"  Enriched {enriched} referrer company field(s)", flush=True)

        company_dir = SAVE_DIR / _safe_dir_name(company_name)
        for project in projects:
            if not isinstance(project, dict):
                continue

            project_name = project.get("name") or project.get("id") or "unnamed-project"
            project_dir = company_dir / _safe_dir_name(project_name)
            project_path = project_dir / "project.json"

            if not project_path.exists():
                stats["new_projects"] += 1

            _write_json(project_path, project)
            stats["projects"] += 1

            if not fetch_files or not report._is_included(project):
                continue

            stats["eligible_projects"] += 1
            try:
                files = report._fetch_project_files(search, api_key, project.get("id"))
                _write_json(project_dir / "inspect_files.json", files)
                stats["file_manifests"] += 1
            except Exception as exc:
                print(f"  WARNING: files for {project_name}: {exc}", flush=True)

    return stats


def run_report(output_file, style):
    cmd = [sys.executable, str(BASE_DIR / "report.py"), "--out", output_file, f"-{style}"]
    subprocess.run(cmd, cwd=str(BASE_DIR), check=True)


def open_report(output_file):
    subprocess.run(["open", str(BASE_DIR / "reports" / output_file)], check=False)


CF_PROJECT = "hostreports"
WRANGLER   = r"C:\Users\jackson\AppData\Roaming\npm\wrangler.cmd"


def deploy_report():
    token = os.environ.get("Cloudflare_API_TOKEN")
    if not token:
        print("WARNING: Cloudflare_API_TOKEN not set — skipping Cloudflare deploy.", flush=True)
        return

    reports_dir = str(BASE_DIR / "reports")
    cmd = [WRANGLER, "pages", "deploy", reports_dir, f"--project-name={CF_PROJECT}"]
    env = {**os.environ, "CLOUDFLARE_API_TOKEN": token,
           "CLOUDFLARE_ACCOUNT_ID": os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")}
    print(f"Deploying reports/ to Cloudflare Pages ({CF_PROJECT})...", flush=True)
    result = subprocess.run(cmd, env=env, cwd=str(BASE_DIR), capture_output=True,
                            encoding="utf-8", errors="replace")
    combined = (result.stdout or "") + (result.stderr or "")

    # wrangler prints the snapshot URL as: https://<hash>.hostreports.pages.dev
    match = re.search(r"https://[a-z0-9\-]+\.hostreports\.pages\.dev\S*", combined)
    if result.returncode == 0:
        snapshot_url = match.group(0).rstrip(".") if match else f"https://{CF_PROJECT}.pages.dev"
        print(f"  Deployed snapshot: {snapshot_url}", flush=True)
        return snapshot_url
    else:
        print(combined, flush=True)
        print(f"  WARNING: wrangler exited with code {result.returncode}", flush=True)
        return None


def main():
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="Refresh Albi project data and generate report.html."
    )
    parser.set_defaults(style="1")
    parser.add_argument(
        "--out",
        default=datetime.now().strftime("%m%d%y") + "Report.html",
        help="Output report filename (default: mmddyyReport.html)",
    )
    parser.add_argument(
        "--style",
        choices=("1", "2", "3", "4"),
        help="Visual style number passed to report.py: 1 clean, 2 dark, 3 audit, 4 tech.",
    )
    parser.add_argument("-1", action="store_const", const="1", dest="style",
                        help="Style 1: clean minimal / corporate SaaS.")
    parser.add_argument("-2", action="store_const", const="2", dest="style",
                        help="Style 2: executive dark / Linear-like.")
    parser.add_argument("-3", action="store_const", const="3", dest="style",
                        help="Style 3: government audit / newspaper.")
    parser.add_argument("-4", action="store_const", const="4", dest="style",
                        help="Style 4: tech blueprint / terminal neon.")
    parser.add_argument(
        "--skip-files",
        action="store_true",
        help="Skip fetching project file manifests during the refresh step.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        dest="open_output",
        help="Open the generated HTML report after it is written.",
    )
    args = parser.parse_args()

    stats = refresh_saved_projects(fetch_files=not args.skip_files)
    print(
        "Refresh complete: "
        f"{stats['projects']} project(s), "
        f"{stats['new_projects']} new, "
        f"{stats['file_manifests']} file manifest(s)",
        flush=True,
    )

    run_report(args.out, args.style)
    snapshot_url = deploy_report()

    report_url = f"{snapshot_url}/{args.out}" if snapshot_url else None
    print("Sending report email...", flush=True)
    _email_mod.send_report_link(report_url, args.out)

    if args.open_output:
        open_report(args.out)


if __name__ == "__main__":
    main()
