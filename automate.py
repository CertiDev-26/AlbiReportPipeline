#!/usr/bin/env python3
"""Refresh saved Albi project data, then generate the HTML report."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import report
import search


BASE_DIR = Path(__file__).resolve().parent
SAVE_DIR = BASE_DIR / "saved"


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
    subprocess.run(["open", str(BASE_DIR / output_file)], check=False)


def main():
    parser = argparse.ArgumentParser(
        description="Refresh Albi project data and generate report.html."
    )
    parser.set_defaults(style="1")
    parser.add_argument(
        "--out",
        default="report.html",
        help="Output report filename passed to report.py (default: report.html)",
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

    if args.open_output:
        open_report(args.out)


if __name__ == "__main__":
    main()
