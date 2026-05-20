# Generates an HTML report for configured CD job types with referral info,
# aging analysis, and missing artifact flags — modeled after Albi's Red Report.
#
# Usage:
#   python report.py               # reads saved/ folder, writes report.html
#   python report.py --api         # pulls live data from Albi API
#   python report.py --out my.html # custom output filename
#   python report.py -2            # use visual style 2

import argparse
import json
import os
import re
import sys
from html import escape
from datetime import datetime, timezone
from collections import defaultdict

from dotenv import load_dotenv

_BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE, ".env"))

SAVE_DIR       = os.path.join(_BASE, "saved")
CUTOFF_DATE    = datetime(2025, 3, 27, tzinfo=timezone.utc)
JOB_TYPES = [
    ("ASB", "Asbestos"),
    ("BIO", "Biohazard"),
    ("BT", "Boardup/Tarp"),
    ("CLN", "Clean"),
    ("CON", "Contents"),
    ("DMO", "Demo"),
    ("EQP", "Equipment Rental"),
    ("FIR", "Fire"),
    ("INSPECT", "Inspection"),
    ("LAB", "Labor"),
    ("LED", "Lead"),
    ("MLD", "Mold"),
    ("NJ", "No Job#"),
    ("OFF", "Office Cleaning"),
    ("PAP", "Paperwork"),
    ("RBLD", "Rebuild"),
    ("EXT", "Roofing/Exterior"),
    ("TST", "Testing"),
    ("VEH", "Trucks/Shop"),
    ("WH", "Warehouse"),
    ("WTR", "Water"),
]
JOB_TYPE_ORDER = [code for code, _label in JOB_TYPES]
JOB_TYPE_LABELS = dict(JOB_TYPES)
INCLUDED_TYPES = set(JOB_TYPE_ORDER)
DEFAULT_GRADE_FILTER = "red"
EXCLUDED_STATUSES = {"closed", "lost"}
FILE_MANIFEST_KEY = "_file_manifest"
FILE_MANIFEST_MISSING_KEY = "_file_manifest_missing"
REFERRER_CONTACT_ORGANIZATION_KEY = "referrerContactOrganizationName"
CHECK_MARK = "✅"
X_MARK = "❌"
GRADE_ORDER = ["red", "yellow", "green", "none"]
GRADE_LABELS = {"red": "Red", "yellow": "Yellow", "green": "Green", "none": "Unknown"}
GRADE_RANK = {grade: idx for idx, grade in enumerate(GRADE_ORDER)}
STYLE_LABELS = {
    "1": "Clean Minimal",
    "2": "Executive Dark",
    "3": "Government Audit",
    "4": "Tech Blueprint",
}

STAGE_ORDER = ["Pre-Sales", "In-Production", "Collections", "Other"]
STAGE_MAP = {
    "New Lead":              "Pre-Sales",
    "New":                   "Pre-Sales",
    "Scheduled for Inspection": "Pre-Sales",
    "Estimating":            "Pre-Sales",
    "Planning/Budget":       "Pre-Sales",
    "Planning/Budgeting":     "Pre-Sales",
    "Inspected":             "In-Production",
    "Work In Progress":      "In-Production",
    "Compliance Review":     "In-Production",
    "Ready to Invoice":      "In-Production",
    "Insurance Negotiation": "Collections",
    "Insurance Negotiations": "Collections",
    "Collections":           "Collections",
    "Receivables":           "Collections",
    "Legal":                 "Collections",
}

# Per-status aging thresholds (green_hours, yellow_hours, red_hours, target_label)
# Sourced from the Albi status threshold screen.
STATUS_THRESHOLDS = {
    "New Lead":              (24, 48, 48,  "<24h green / <48h yellow"),
    "New":                   (24, 48, 48,  "<24h green / <48h yellow"),
    "Scheduled for Inspection": (24, 48, 48, "<24h green / <48h yellow"),
    "Inspected":             (24, 48, 48,  "<24h green / <48h yellow"),
    "Planning/Budget":       (24, 48, 48,  "<24h green / <48h yellow"),
    "Planning/Budgeting":     (24, 48, 48,  "<24h green / <48h yellow"),
    "Work In Progress":      (72, 168, 168, "<72h green / <168h yellow"),
    "Compliance Review":     (24, 48, 48,  "<24h green / <48h yellow"),
    "Estimating":            (72, 168, 168, "<72h green / <168h yellow"),
    "Ready to Invoice":      (24, 48, 48,  "<24h green / <48h yellow"),
    "Insurance Negotiation": (168, 336, 336, "<168h green / <336h yellow"),
    "Insurance Negotiations": (168, 336, 336, "<168h green / <336h yellow"),
    "Collections":           (336, 672, 672, "<336h green / <672h yellow"),
    "Receivables":           (168, 336, 336, "<168h green / <336h yellow"),
    "Legal":                 (24, 24, 24,  "<24h green"),
}

GRADE_COLORS = {"green": "#16a34a", "yellow": "#d97706", "red": "#dc2626", "none": "#2563eb"}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _job_type(name):
    parts = (name or "").split("-")
    return parts[-1].upper() if parts else ""


def _parse_dt(raw):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _strftime_no_pad(dt, fmt):
    # %-d / %-m / %-I strip leading zeros on Linux; %#d / %#m / %#I on Windows
    if sys.platform == "win32":
        fmt = fmt.replace("%-", "%#")
    return dt.strftime(fmt)


def _fmt_date(raw):
    dt = _parse_dt(raw)
    if not dt:
        return None
    return _strftime_no_pad(dt, "%-m/%-d/%Y")


def _is_included(p):
    name = p.get("name") or ""
    if not name.startswith("CD-"):
        return False
    if _job_type(name) not in INCLUDED_TYPES:
        return False
    if (p.get("status") or "").strip().lower() in EXCLUDED_STATUSES:
        return False
    dt = _parse_dt(p.get("createdAt"))
    return bool(dt and dt >= CUTOFF_DATE)


def _stage(p):
    return STAGE_MAP.get(p.get("status") or "", "Other")


def _days_active(p):
    try:
        start = _parse_dt(p.get("createdAt"))
        if not start:
            return None
        end = datetime.now(timezone.utc)
        if p.get("closedBoolean"):
            raw = p.get("closed") or ""
            if raw:
                end = _parse_dt(raw) or end
        return max(0, (end - start).days)
    except Exception:
        return None


def _fmt_hours(hours):
    if hours is None:
        return None
    if hours < 24:
        return f"{hours:g}h"
    days = round(hours / 24, 1)
    return f"{days:g}d ({hours:g}h)"


def _aging_audit(p):
    """Return (hours_in_stage, grade, audit_text) based on statusDate and thresholds."""
    status = p.get("status") or ""
    status_raw = p.get("statusDate") or p.get("createdAt") or ""
    source = "status date" if p.get("statusDate") else "created date fallback"
    if not status_raw:
        return None, "none", None

    try:
        status_dt = _parse_dt(status_raw)
        if not status_dt:
            return None, "none", None
        now = datetime.now(timezone.utc)
        hours_in_stage = round((now - status_dt).total_seconds() / 3600, 1)
    except Exception:
        return None, "none", None

    thresh = STATUS_THRESHOLDS.get(status)
    if not thresh:
        return hours_in_stage, "none", (
            f"Aging: {_fmt_hours(hours_in_stage)} in status \"{_h(status)}\" "
            f"(no threshold defined for this status)."
        )

    green_h, yellow_h, red_h, target = thresh

    if hours_in_stage >= red_h:
        grade = "red"
    elif hours_in_stage >= green_h:
        grade = "yellow"
    else:
        grade = "green"

    status_date_fmt = _strftime_no_pad(status_dt, "%-m/%-d/%Y")
    audit_text = (
        f"Aging: {_fmt_hours(hours_in_stage)} in status \"{_h(status)}\" "
        f"(since {status_date_fmt}, {source}). "
        f"Thresholds: green &lt; {green_h:g}h, yellow &lt; {yellow_h:g}h, red &gt;= {red_h:g}h. "
        f"Target: {_h(target)}."
    )
    return hours_in_stage, grade, audit_text


def _report_sort_key(p):
    age_hours, grade, _ = _aging_audit(p)
    return (
        GRADE_RANK.get(grade, len(GRADE_ORDER)),
        -(age_hours or 0),
        p.get("name") or "",
    )


def _full_address(p):
    parts = [p.get("address1") or p.get("address") or "",
             p.get("city") or "", p.get("state") or "", p.get("zipCode") or ""]
    return ", ".join(x for x in parts if x) or None


def _norm_email(value):
    return str(value or "").strip().lower()


def _norm_phone(value):
    return re.sub(r"\D+", "", str(value or ""))


def _norm_name(value):
    text = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    return re.sub(r"(.)\1+", r"\1", text)


def _parenthetical_name(value):
    match = re.search(r"\(([^()]+)\)\s*$", str(value or "").strip())
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def _company_without_contact_suffix(company, individual=None):
    if not company:
        return None
    text = str(company).strip()
    match = re.match(r"^(.*?)\s+\(([^()]+)\)\s*$", text)
    if not match:
        return text
    company_name = match.group(1).strip()
    suffix_name = match.group(2).strip()
    if individual and _norm_name(suffix_name) == _norm_name(individual):
        return company_name or text
    return text


def _referrer_company(p):
    company = (
        p.get("referrerOrganizationName")
        or p.get(REFERRER_CONTACT_ORGANIZATION_KEY)
    )
    return _company_without_contact_suffix(company, _referrer_individual(p))


def _contact_company(contact):
    return (
        contact.get("organizationName")
        or contact.get("parentOrganizationName")
        or None
    )


def _contact_name(contact):
    return (
        contact.get("name")
        or " ".join(
            x for x in (contact.get("firstName"), contact.get("lastName")) if x
        )
        or None
    )


def _add_referrer_index(index, *, company, names=(), emails=(), phones=()):
    if not company:
        return
    for email in emails:
        key = _norm_email(email)
        if key:
            index["email"].setdefault(key, company)
    for phone in phones:
        key = _norm_phone(phone)
        if key:
            index["phone"].setdefault(key, company)
    for name in names:
        key = _norm_name(name)
        if key:
            index["name"].setdefault(key, company)


def _referrer_lookup_index(projects, contacts=None):
    index = {"email": {}, "phone": {}, "name": {}}

    for contact in contacts or []:
        if not isinstance(contact, dict):
            continue
        _add_referrer_index(
            index,
            company=_contact_company(contact),
            names=(_contact_name(contact),),
            emails=(contact.get("email"),),
            phones=(contact.get("phoneNumber"), contact.get("mobileNumber")),
        )

    for p in projects:
        company = (
            p.get("referrerOrganizationName")
            or p.get(REFERRER_CONTACT_ORGANIZATION_KEY)
        )
        if not company:
            continue
        parenthetical = _parenthetical_name(company)
        _add_referrer_index(
            index,
            company=company,
            names=(p.get("referrerContactName"), parenthetical),
            emails=(p.get("referrerContactEmail"), p.get("referrerEmail")),
            phones=(p.get("referrerContactPhoneNumber"), p.get("referrerPhoneNumber")),
        )

    return index


def enrich_referrer_companies(projects, contacts=None):
    """Fill missing referrer company values from contact or historical referrer data."""
    index = _referrer_lookup_index(projects, contacts=contacts)
    enriched = 0

    for p in projects:
        if p.get("referrerOrganizationName") or p.get(REFERRER_CONTACT_ORGANIZATION_KEY):
            continue

        candidates = [
            ("email", _norm_email(p.get("referrerContactEmail"))),
            ("email", _norm_email(p.get("referrerEmail"))),
            ("phone", _norm_phone(p.get("referrerContactPhoneNumber"))),
            ("phone", _norm_phone(p.get("referrerPhoneNumber"))),
            ("name", _norm_name(p.get("referrerContactName"))),
            ("name", _norm_name(p.get("referrerName"))),
        ]
        for kind, key in candidates:
            if not key:
                continue
            company = index[kind].get(key)
            if company:
                p[REFERRER_CONTACT_ORGANIZATION_KEY] = company
                enriched += 1
                break

    return enriched


def _referrer_individual(p):
    return (p.get("referrerContactName")
            or p.get("referrerName")
            or None)


def _referrer_phone(p):
    return (p.get("referrerContactPhoneNumber")
            or p.get("referrerPhoneNumber")
            or None)


def _needs_referrer_company_enrichment(projects):
    return any(
        not (p.get("referrerOrganizationName")
             or p.get(REFERRER_CONTACT_ORGANIZATION_KEY))
        and _referrer_individual(p)
        for p in projects
    )


def _is_empty(val):
    return val is None or val == ""


def _h(val):
    return escape(str(val), quote=True)


def _display_value(val, missing=None):
    is_missing = _is_empty(val) if missing is None else missing
    if is_missing:
        return '<span class="missing-value">—</span>'
    return _h(val)


def _row(label, val, missing=None, raw=False):
    is_missing = _is_empty(val) if missing is None else missing
    if is_missing:
        rendered = '<span class="missing-value">—</span>'
    elif raw:
        rendered = str(val)
    else:
        rendered = _h(val)
    return f'<tr><td class="lbl">{_h(label)}</td><td>{rendered}</td></tr>'


def _lower_blob(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, sort_keys=True).lower()
        except Exception:
            return str(value).lower()
    return str(value).lower()


def _record_text(record, fields):
    return " ".join(_lower_blob(record.get(field)) for field in fields)


def _file_manifest(p):
    files = p.get(FILE_MANIFEST_KEY)
    return files if isinstance(files, list) else []


def _is_usable_file(record):
    if record.get("deleted") is True:
        return False
    if record.get("active") is False:
        return False
    if record.get("notUploaded") is True:
        return False
    return bool(record.get("versionId") or record.get("fileUri"))


def _file_created_after_project(record, p):
    project_dt = _parse_dt(p.get("createdAt"))
    file_dt = _parse_dt(record.get("createdAt"))
    return bool(project_dt and file_dt and file_dt > project_dt)


def _status_or_tag_has(record, *needles):
    haystack = _record_text(record, ("status", "projectDocumentTags", "metadata"))
    return any(re.search(rf"\b{re.escape(needle)}\b", haystack) for needle in needles)


def _is_scope_file(record):
    name = _lower_blob(record.get("fileName"))
    return "scope sheet" in name


def _scope_sheet_status(p):
    for record in _file_manifest(p):
        if not _is_scope_file(record):
            continue
        if record.get("deleted") is True or record.get("active") is False:
            continue
        if _status_or_tag_has(record, "complete", "completed"):
            return True, _fmt_date(record.get("createdAt"))
        if _is_usable_file(record) and _file_created_after_project(record, p):
            return True, _fmt_date(record.get("createdAt"))
    return False, None


def _is_contract_file(record):
    name = _lower_blob(record.get("fileName")).replace("_", " ")
    if _lower_blob(record.get("folderName")) == "emails":
        return False
    return (
        "emergency services work agreement" in name
        or "commercial service agreement" in name
    )


def _signing_status_is_signed(record):
    status = record.get("signingStatus")
    try:
        return int(status) >= 4
    except Exception:
        return False


def _contract_is_signed(record):
    name = _lower_blob(record.get("fileName"))
    return (
        _status_or_tag_has(record, "signed")
        or _signing_status_is_signed(record)
        or ("signed" in name and _is_usable_file(record))
    )


def _signed_contract_status(p):
    for record in _file_manifest(p):
        if not _is_contract_file(record):
            continue
        if record.get("deleted") is True or record.get("active") is False:
            continue
        if _contract_is_signed(record):
            return True, _fmt_date(record.get("createdAt"))
    return False, None


def _documentation_status(p):
    scope_complete, scope_completed_at = _scope_sheet_status(p)
    signed_complete, signed_at = _signed_contract_status(p)
    return {
        "scope_complete": scope_complete,
        "scope_completed_at": scope_completed_at,
        "signed_contract": signed_complete,
        "signed_at": signed_at,
    }


def _missing_artifacts(p, docs=None):
    """Return missing report field labels for rows shown in the HTML."""
    docs = docs or _documentation_status(p)
    missing = []
    field_values = [
        ("Customer Name", p.get("customerName")),
        ("Address", _full_address(p)),
        ("Customer Phone", p.get("customerPhoneNumber")),
        ("Customer Email", p.get("customerEmail")),
        ("Referred Company", _referrer_company(p)),
        ("Referrer", _referrer_individual(p)),
        ("Referrer Phone", _referrer_phone(p)),
        ("Referrer Relationship", p.get("referralSource")),
        ("Insurance Company", p.get("insuranceCompany")),
        ("Insurance Claim Number", p.get("insuranceClaimNumber")),
    ]

    for label, value in field_values:
        if _is_empty(value):
            missing.append(f"{label} missing")

    if not docs["scope_complete"]:
        missing.append("Scope Sheet missing")
    if _is_empty(docs["scope_completed_at"]):
        missing.append("Scope Sheet Completed At missing")
    if not docs["signed_contract"]:
        missing.append("Signed Contract missing")
    if _is_empty(docs["signed_at"]):
        missing.append("Signed At missing")

    return missing

# ── Data loading ──────────────────────────────────────────────────────────────

def _load_local():
    projects = []
    for company_dir in sorted(os.listdir(SAVE_DIR)):
        cp = os.path.join(SAVE_DIR, company_dir)
        if not os.path.isdir(cp):
            continue
        for project_dir in sorted(os.listdir(cp)):
            pj = os.path.join(cp, project_dir, "project.json")
            if not os.path.exists(pj):
                continue
            try:
                with open(pj, encoding="utf-8") as f:
                    project = json.load(f)
                inspect_path = os.path.join(cp, project_dir, "inspect_files.json")
                if os.path.exists(inspect_path):
                    with open(inspect_path, encoding="utf-8") as f:
                        project[FILE_MANIFEST_KEY] = json.load(f)
                    project[FILE_MANIFEST_MISSING_KEY] = False
                else:
                    project[FILE_MANIFEST_KEY] = []
                    project[FILE_MANIFEST_MISSING_KEY] = True
                projects.append(project)
            except Exception as e:
                print(f"  WARNING: {pj}: {e}")
    enrich_referrer_companies(projects)
    return projects


def _fetch_project_files(search, api_key, project_id):
    files = []
    page = 1
    while True:
        data = search.get("Projects/GetProjectFiles", api_key,
                          params={"projectId": project_id, "page": page, "pageSize": 100})
        if not data:
            break
        batch = data if isinstance(data, list) else data.get("data", [])
        if not batch:
            break
        files.extend(batch)
        pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
        if pagination:
            if page >= pagination.get("totalPages", 1):
                break
        elif len(batch) < 100:
            break
        page += 1
    return files


def _load_api():
    sys.path.insert(0, _BASE)
    import search
    projects = []
    for company_name, api_key in search.COMPANIES:
        if not api_key:
            continue
        print(f"  Fetching {company_name}...", flush=True)
        try:
            batch = search.get_all_projects(api_key)
            contacts = []
            if _needs_referrer_company_enrichment(batch):
                try:
                    contacts = search.get_all_contacts(api_key)
                except Exception as e:
                    print(f"    WARNING: contacts lookup failed: {e}", flush=True)
            enriched = enrich_referrer_companies(batch, contacts=contacts)
            if enriched:
                print(f"    Enriched {enriched} referrer company field(s)", flush=True)
            eligible = [p for p in batch if _is_included(p)]
            if eligible:
                print(f"    Fetching file manifests for {len(eligible)} matching project(s)...", flush=True)
            for idx, p in enumerate(eligible, 1):
                if idx == 1 or idx % 10 == 0 or idx == len(eligible):
                    print(f"      {idx}/{len(eligible)} manifests", flush=True)
                try:
                    p[FILE_MANIFEST_KEY] = _fetch_project_files(search, api_key, p.get("id"))
                    p[FILE_MANIFEST_MISSING_KEY] = False
                except Exception as e:
                    p[FILE_MANIFEST_KEY] = []
                    p[FILE_MANIFEST_MISSING_KEY] = True
                    print(f"    WARNING: files for {p.get('name') or p.get('id')}: {e}", flush=True)
            projects.extend(batch)
            print(f"    {len(batch)} project(s)", flush=True)
        except Exception as e:
            print(f"  ERROR {company_name}: {e}", flush=True)
    return projects

# ── HTML rendering ────────────────────────────────────────────────────────────

def _section_html(title, rows):
    return f"""
          <div class="detail-section">
            <div class="section-label">{_h(title)}</div>
            <table class="info-table">{''.join(rows)}</table>
          </div>"""


def _check_html(ok):
    cls = "status-ok" if ok else "status-no"
    mark = CHECK_MARK if ok else X_MARK
    return f'<span class="{cls}">{mark}</span>'


def _filter_button(group, value, label, count, active=False, extra_class=""):
    active_cls = " is-active" if active else ""
    extra_cls = f" {extra_class}" if extra_class else ""
    pressed = "true" if active else "false"
    return (
        f'<button type="button" class="filter-btn{extra_cls}{active_cls}" '
        f'data-filter-group="{_h(group)}" data-filter-value="{_h(value)}" '
        f'aria-pressed="{pressed}">'
        f'{_h(label)} <span class="filter-count">{count}</span>'
        f'</button>'
    )


def _card_html(p):
    name   = p.get("name")
    job_type = _job_type(name)
    stage = _stage(p)
    days   = _days_active(p)
    status = p.get("status")
    created = _fmt_date(p.get("createdAt"))

    days_in_stage, grade, audit_text = _aging_audit(p)
    border_color = GRADE_COLORS[grade]
    grade_label = GRADE_LABELS.get(grade, "Unknown")
    days_str     = f"{days} days" if days is not None else None
    docs         = _documentation_status(p)
    scope_check  = _check_html(docs["scope_complete"])
    signed_check = _check_html(docs["signed_contract"])

    # Customer section
    customer_html = _section_html("Customer", [
        _row("Customer Name", p.get("customerName")),
        _row("Address", _full_address(p)),
        _row("Phone", p.get("customerPhoneNumber")),
        _row("Email", p.get("customerEmail")),
    ])

    # Referral section
    referral_html = _section_html("Referral", [
        _row("Referred Company", _referrer_company(p)),
        _row("Referrer", _referrer_individual(p)),
        _row("Referrer Phone", _referrer_phone(p)),
        _row("Relationship", p.get("referralSource")),
    ])

    # Insurance section
    insurance_html = _section_html("Insurance", [
        _row("Insurance Company", p.get("insuranceCompany")),
        _row("Claim Number", p.get("insuranceClaimNumber")),
    ])

    # Documentation section
    documentation_html = _section_html("Documentation", [
        _row("Scope Sheet", scope_check, missing=False, raw=True),
        _row("Scope Sheet Completed At", docs["scope_completed_at"]),
        _row("Signed Contract", signed_check, missing=False, raw=True),
        _row("Signed At", docs["signed_at"]),
    ])

    # Audit reasons section
    audit_html = ""
    if audit_text:
        grade_badge = f'<span class="grade-badge grade-{grade}">{grade.upper()}</span>'
        audit_html = f"""
      <div class="audit-block">
        <div class="section-label audit-label" style="color:{border_color};border-color:{border_color};">
          Audit Reasons {grade_badge}
        </div>
        <ul class="audit-list">
          <li>{audit_text}</li>
        </ul>
      </div>"""

    # Missing artifacts section
    missing = _missing_artifacts(p, docs)
    artifacts_html = ""
    if missing:
        items = "".join(f"<li>{_h(m)}</li>" for m in missing)
        artifacts_html = f"""
      <div class="audit-block">
        <div class="section-label audit-label" style="color:#dc2626;border-color:#dc2626;">Missing Artifacts</div>
        <ul class="audit-list missing-list">{items}</ul>
      </div>"""

    return f"""
    <div class="card grade-card grade-card-{_h(grade)}" data-job-type="{_h(job_type)}" data-stage="{_h(stage)}" data-grade="{_h(grade)}" style="border-left-color:{border_color}">
      <div class="card-header" style="border-bottom-color:{border_color}22">
        <span class="job-num" style="color:{border_color}">{_display_value(name)}</span>
        <span class="grade-pill grade-pill-{_h(grade)}">{_h(grade_label)}</span>
        <span class="meta">Status: {_display_value(status)}&nbsp;&nbsp;|&nbsp;&nbsp;Days Active: {_display_value(days_str)}&nbsp;&nbsp;|&nbsp;&nbsp;Created: {_display_value(created)}</span>
      </div>
      <div class="card-body">
        <div class="details-grid">
          {customer_html}
          {referral_html}
          {insurance_html}
          {documentation_html}
        </div>
        <div class="audit-col">
          {audit_html}
          {artifacts_html}
        </div>
      </div>
    </div>"""


def _style_css(style):
    styles = {
        "1": """
    /* Style 1: Clean Minimal / Corporate SaaS */
    :root {
      --bg: #f7f8fa;
      --paper: #ffffff;
      --paper-soft: #fbfbfc;
      --ink: #1f2937;
      --muted: #6b7280;
      --line: #e5e7eb;
      --blue: #4f46e5;
      --cyan: #0e7490;
      --violet: #6d28d9;
      --green: #15803d;
      --amber: #b45309;
      --red: #b91c1c;
      --shadow: 0 8px 24px rgba(17, 24, 39, 0.06);
    }
    body { background: var(--bg); color: var(--ink); }
    .report-header {
      background: #ffffff;
      color: var(--ink);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }
    .report-header h1 { font-size: 26px; font-weight: 780; }
    .report-header .subtitle { color: var(--muted); }
    .filters, .card, .detail-section, .audit-block, .no-results {
      border-color: var(--line);
      box-shadow: var(--shadow);
    }
    .summary {
      gap: 8px;
      background: color-mix(in srgb, var(--bg) 92%, transparent);
    }
    .filter-btn, .chip {
      border-radius: 6px;
      background: #ffffff;
      box-shadow: none;
    }
    .filter-btn:hover, button.chip:hover { border-color: #a5b4fc; }
    .filter-btn.is-active, .chip-filter.is-active {
      border-color: #111827;
      border-top-color: #111827;
      background: #111827;
      color: #ffffff;
    }
    .grade-filter-btn { min-height: 46px; box-shadow: none; }
    .stage-heading {
      position: static;
      background: #ffffff;
      color: var(--ink);
      border: 1px solid var(--line);
      border-left: 5px solid #111827;
      box-shadow: none;
    }
    .stage-heading-red { border-left-color: var(--red); }
    .stage-heading-yellow { border-left-color: var(--amber); }
    .stage-heading-green { border-left-color: var(--green); }
    .stage-count { background: #f3f4f6; color: #111827; }
    .card { border-left-width: 5px; }
    .card:hover { box-shadow: 0 12px 28px rgba(17, 24, 39, 0.09); }
    .card-header { background: #ffffff; }
    .grade-card-red .card-header,
    .grade-card-yellow .card-header,
    .grade-card-green .card-header { background: #ffffff; }
    .section-label {
      background: #f3f4f6;
      color: #374151;
      border-bottom-color: var(--line);
    }
    .detail-section:nth-child(n) .section-label {
      background: #f3f4f6;
      color: #374151;
      border-bottom-color: var(--line);
    }
    .info-table tr + tr { border-top-color: var(--line); }
    .audit-list li, .meta { color: #4b5563; }
""",
        "2": """
    /* Style 2: Executive Dark / Linear-like */
    :root {
      --bg: #080b12;
      --paper: #111827;
      --paper-soft: #0f172a;
      --ink: #f8fafc;
      --muted: #9ca3af;
      --line: #243044;
      --blue: #14b8a6;
      --cyan: #38bdf8;
      --violet: #a78bfa;
      --green: #22c55e;
      --amber: #f5c451;
      --red: #fb7185;
      --shadow: 0 22px 60px rgba(0, 0, 0, 0.38);
    }
    html { background: radial-gradient(circle at 20% 0%, #1e293b 0, #080b12 34%, #05070c 100%); }
    body {
      background: transparent;
      color: var(--ink);
    }
    .report-header {
      background: linear-gradient(135deg, #111827, #172033 60%, #0f766e);
      color: #f8fafc;
      border: 1px solid #334155;
      box-shadow: var(--shadow);
    }
    .report-header h1 {
      font-family: Georgia, "Times New Roman", serif;
      font-size: 31px;
      font-weight: 700;
    }
    .report-header .subtitle { color: #cbd5e1; }
    .filters {
      background: rgba(15, 23, 42, 0.92);
      border-color: #334155;
      box-shadow: var(--shadow);
    }
    .filter-label, .filter-status, .filter-count { color: #94a3b8; }
    .filter-btn, .chip {
      background: #0f172a;
      color: #e5e7eb;
      border-color: #334155;
      box-shadow: none;
    }
    .filter-btn:hover, button.chip:hover {
      border-color: var(--blue);
      box-shadow: 0 0 0 1px rgba(20, 184, 166, 0.35);
    }
    .filter-btn.is-active, .chip-filter.is-active {
      background: linear-gradient(135deg, #0f766e, #312e81);
      border-color: #5eead4;
      border-top-color: #5eead4;
      color: #ffffff;
    }
    .filter-btn.is-active .filter-count, .chip-filter.is-active strong { color: #ffffff; }
    .grade-filter-btn { box-shadow: inset 0 1px rgba(255,255,255,0.08); }
    .grade-filter-red { background: rgba(127, 29, 29, 0.38); border-color: rgba(251, 113, 133, 0.45); }
    .grade-filter-yellow { background: rgba(120, 53, 15, 0.35); border-color: rgba(245, 196, 81, 0.48); }
    .grade-filter-green { background: rgba(20, 83, 45, 0.36); border-color: rgba(34, 197, 94, 0.48); }
    .summary { background: rgba(8, 11, 18, 0.82); }
    .chip strong { color: #f8fafc; }
    .stage-heading {
      background: #0f172a;
      border: 1px solid #334155;
      color: #f8fafc;
      box-shadow: var(--shadow);
    }
    .stage-heading-red { background: linear-gradient(90deg, #7f1d1d, #111827); }
    .stage-heading-yellow { background: linear-gradient(90deg, #78350f, #111827); }
    .stage-heading-green { background: linear-gradient(90deg, #14532d, #111827); }
    .stage-count { background: rgba(255,255,255,0.12); color: #f8fafc; }
    .card {
      background: #0f172a;
      border-color: #263449;
      box-shadow: var(--shadow);
    }
    .card-header,
    .grade-card-red .card-header,
    .grade-card-yellow .card-header,
    .grade-card-green .card-header {
      background: #111827;
      border-bottom-color: #263449;
    }
    .meta, .audit-list li { color: #cbd5e1; }
    .detail-section, .audit-block { background: #101827; border-color: #263449; }
    .section-label,
    .detail-section:nth-child(n) .section-label {
      background: #182235;
      color: #e2e8f0;
      border-bottom-color: #263449;
    }
    .info-table tr + tr { border-top-color: #263449; }
    .info-table td.lbl { color: #94a3b8; }
    .no-results { background: #0f172a; border-color: #263449; color: #cbd5e1; }
""",
        "3": """
    /* Style 3: Government / Audit / Newspaper */
    :root {
      --bg: #ffffff;
      --paper: #ffffff;
      --paper-soft: #ffffff;
      --ink: #000000;
      --muted: #333333;
      --line: #000000;
      --blue: #000000;
      --cyan: #000000;
      --violet: #000000;
      --green: #0f7a1f;
      --amber: #8a5a00;
      --red: #b00020;
      --shadow: none;
    }
    body {
      background: #ffffff;
      color: #000000;
      font-family: Arial, Helvetica, sans-serif;
      font-size: 12px;
      padding: 14px;
    }
    .report-header {
      background: #ffffff;
      color: #000000;
      border: 3px solid #000000;
      border-left: 14px solid var(--red);
      border-radius: 0;
      box-shadow: none;
      padding: 12px 14px;
      text-transform: uppercase;
    }
    .report-header h1 { font-size: 24px; letter-spacing: 0; }
    .report-header .subtitle { color: #222222; font-size: 11px; }
    .filters {
      border: 2px solid #000000;
      border-radius: 0;
      box-shadow: none;
      padding: 8px;
      gap: 6px;
    }
    .filter-row { gap: 6px; }
    .filter-label { color: #000000; width: 58px; }
    .filter-btn, .chip {
      background: #ffffff;
      color: #000000;
      border: 2px solid #000000;
      border-radius: 0;
      box-shadow: none;
      min-height: 30px;
      padding: 6px 8px;
    }
    .filter-btn:hover, button.chip:hover { background: #f2f2f2; border-color: #000000; }
    .filter-btn.is-active, .chip-filter.is-active {
      background: #000000;
      border-color: #000000;
      border-top-color: #000000;
      color: #ffffff;
    }
    .grade-filter-btn { min-height: 40px; font-size: 13px; }
    .grade-filter-red { color: var(--red); }
    .grade-filter-yellow { color: #704800; }
    .grade-filter-green { color: #0f7a1f; }
    .summary {
      position: static;
      grid-template-columns: repeat(auto-fit, minmax(82px, 1fr));
      background: #ffffff;
      backdrop-filter: none;
      gap: 6px;
      margin-bottom: 10px;
    }
    .chip { border-top-width: 2px; }
    .chip strong { font-size: 20px; color: #000000; }
    .chip-filter.is-active strong { color: #ffffff; }
    .stage-section { margin-bottom: 12px; }
    .stage-heading {
      position: static;
      background: #000000;
      color: #ffffff;
      border-radius: 0;
      box-shadow: none;
      margin-bottom: 0;
      padding: 7px 9px;
    }
    .stage-heading-red { background: var(--red); }
    .stage-heading-yellow { background: #6b4f00; }
    .stage-heading-green { background: #0f7a1f; }
    .stage-count { background: #ffffff; color: #000000; border-radius: 0; }
    .card {
      border: 2px solid #000000;
      border-left: 10px solid #000000;
      border-radius: 0;
      box-shadow: none;
      margin-bottom: 0;
    }
    .card:hover { box-shadow: none; }
    .card-header,
    .grade-card-red .card-header,
    .grade-card-yellow .card-header,
    .grade-card-green .card-header {
      background: #ffffff;
      border-bottom: 2px solid #000000;
      padding: 7px 9px;
    }
    .job-num { font-size: 15px; }
    .grade-pill { border-radius: 0; padding: 3px 7px; }
    .meta { color: #111111; font-size: 12px; }
    .card-body {
      padding: 0;
      grid-template-columns: minmax(0, 1fr) minmax(260px, 340px);
      gap: 0;
    }
    .details-grid { gap: 0; grid-template-columns: repeat(4, minmax(190px, 1fr)); }
    .detail-section, .audit-block {
      border: 0;
      border-right: 1px solid #000000;
      border-bottom: 1px solid #000000;
      border-radius: 0;
      background: #ffffff;
    }
    .section-label,
    .detail-section:nth-child(n) .section-label {
      background: #eeeeee;
      color: #000000;
      border-bottom: 1px solid #000000;
      padding: 5px 7px;
    }
    .info-table tr + tr { border-top: 1px solid #000000; }
    .info-table td { padding: 4px 6px; }
    .info-table td.lbl { color: #000000; font-size: 11px; }
    .audit-list { padding: 6px 8px 7px 22px; }
    .audit-list li { color: #000000; font-size: 11px; }
    .no-results { border: 2px solid #000000; border-radius: 0; box-shadow: none; }
""",
        "4": """
    /* Style 4: Tech Blueprint / Terminal Neon */
    :root {
      --bg: #061426;
      --paper: rgba(7, 23, 41, 0.92);
      --paper-soft: rgba(7, 30, 53, 0.82);
      --ink: #d8f7ff;
      --muted: #85b8c8;
      --line: rgba(125, 211, 252, 0.34);
      --blue: #38bdf8;
      --cyan: #22d3ee;
      --violet: #e879f9;
      --green: #39ff88;
      --amber: #facc15;
      --red: #ff3b6b;
      --shadow: 0 0 0 1px rgba(56, 189, 248, 0.22), 0 18px 50px rgba(0, 0, 0, 0.42);
    }
    html {
      background:
        linear-gradient(rgba(56,189,248,0.08) 1px, transparent 1px),
        linear-gradient(90deg, rgba(56,189,248,0.08) 1px, transparent 1px),
        radial-gradient(circle at 18% 8%, rgba(232,121,249,0.18), transparent 28%),
        #061426;
      background-size: 28px 28px, 28px 28px, auto, auto;
    }
    body {
      position: relative;
      background: transparent;
      color: var(--ink);
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background: repeating-linear-gradient(0deg, rgba(255,255,255,0.04), rgba(255,255,255,0.04) 1px, transparent 1px, transparent 4px);
      opacity: 0.18;
      z-index: 999;
    }
    .report-header {
      background: linear-gradient(135deg, rgba(14,116,144,0.88), rgba(88,28,135,0.82));
      color: #effcff;
      border: 1px solid rgba(125, 211, 252, 0.55);
      box-shadow: 0 0 28px rgba(34, 211, 238, 0.22);
    }
    .report-header h1 {
      font-size: 29px;
      text-transform: uppercase;
      text-shadow: 0 0 12px rgba(34, 211, 238, 0.55);
    }
    .report-header .subtitle { color: #bae6fd; }
    .filters {
      background: rgba(7, 23, 41, 0.9);
      border-color: rgba(125, 211, 252, 0.42);
      box-shadow: var(--shadow);
    }
    .filter-label, .filter-status, .filter-count { color: #93c5fd; }
    .filter-btn, .chip {
      background: rgba(8, 30, 54, 0.94);
      color: var(--ink);
      border-color: rgba(125, 211, 252, 0.38);
      box-shadow: 0 0 18px rgba(34, 211, 238, 0.08);
    }
    .filter-btn:hover, button.chip:hover {
      border-color: var(--cyan);
      box-shadow: 0 0 18px rgba(34, 211, 238, 0.24);
    }
    .filter-btn.is-active, .chip-filter.is-active {
      background: linear-gradient(135deg, rgba(34,211,238,0.28), rgba(232,121,249,0.24));
      border-color: var(--cyan);
      border-top-color: var(--cyan);
      color: #ffffff;
      box-shadow: 0 0 22px rgba(34, 211, 238, 0.35);
    }
    .chip strong { color: #ffffff; text-shadow: 0 0 10px rgba(34, 211, 238, 0.4); }
    .grade-filter-red { background: rgba(127, 29, 29, 0.35); border-color: rgba(255, 59, 107, 0.52); }
    .grade-filter-yellow { background: rgba(113, 63, 18, 0.35); border-color: rgba(250, 204, 21, 0.48); }
    .grade-filter-green { background: rgba(20, 83, 45, 0.32); border-color: rgba(57, 255, 136, 0.48); }
    .summary {
      background: rgba(6, 20, 38, 0.78);
      backdrop-filter: blur(10px);
    }
    .stage-heading {
      background: rgba(8, 30, 54, 0.92);
      border: 1px solid rgba(125, 211, 252, 0.45);
      color: #e0fbff;
      box-shadow: var(--shadow);
    }
    .stage-heading-red { background: linear-gradient(90deg, rgba(255,59,107,0.42), rgba(8,30,54,0.92)); }
    .stage-heading-yellow { background: linear-gradient(90deg, rgba(250,204,21,0.33), rgba(8,30,54,0.92)); }
    .stage-heading-green { background: linear-gradient(90deg, rgba(57,255,136,0.28), rgba(8,30,54,0.92)); }
    .stage-count { background: rgba(224, 251, 255, 0.12); color: #e0fbff; }
    .card {
      background: rgba(7, 23, 41, 0.92);
      border-color: rgba(125, 211, 252, 0.34);
      box-shadow: var(--shadow);
    }
    .card:hover { box-shadow: 0 0 0 1px rgba(34, 211, 238, 0.48), 0 20px 54px rgba(0,0,0,0.48); }
    .card-header,
    .grade-card-red .card-header,
    .grade-card-yellow .card-header,
    .grade-card-green .card-header {
      background: rgba(8, 30, 54, 0.9);
      border-bottom-color: rgba(125, 211, 252, 0.28);
    }
    .meta, .audit-list li { color: #b6e9f4; }
    .detail-section, .audit-block {
      background: rgba(8, 30, 54, 0.78);
      border-color: rgba(125, 211, 252, 0.28);
    }
    .section-label,
    .detail-section:nth-child(n) .section-label {
      background: rgba(34, 211, 238, 0.12);
      color: #a5f3fc;
      border-bottom-color: rgba(125, 211, 252, 0.28);
    }
    .info-table tr + tr { border-top-color: rgba(125, 211, 252, 0.20); }
    .info-table td.lbl { color: #93c5fd; }
    .missing-list li, .missing-value { color: #ff87a3; }
    .no-results { background: rgba(7, 23, 41, 0.92); border-color: rgba(125, 211, 252, 0.34); }
""",
    }
    return styles.get(str(style), styles["1"])


def _build_html(projects, style="1"):
    included = [p for p in projects if _is_included(p)]
    included.sort(key=_report_sort_key)

    grade_groups = defaultdict(list)
    for p in included:
        grade_groups[_aging_audit(p)[1]].append(p)

    generated = _strftime_no_pad(datetime.now(), "%B %-d, %Y %-I:%M %p")

    grade_counts = {
        grade: sum(1 for p in included if _aging_audit(p)[1] == grade)
        for grade in GRADE_ORDER
    }
    red_count    = grade_counts["red"]
    yellow_count = grade_counts["yellow"]
    green_count  = grade_counts["green"]

    total_type_counts = {
        t: sum(1 for p in included if _job_type(p.get("name", "")) == t)
        for t in JOB_TYPE_ORDER
    }
    default_type_counts = {
        t: sum(
            1
            for p in included
            if _job_type(p.get("name", "")) == t
            and _aging_audit(p)[1] == DEFAULT_GRADE_FILTER
        )
        for t in JOB_TYPE_ORDER
    }
    stage_counts = {
        stage: sum(1 for p in included if _stage(p) == stage)
        for stage in STAGE_ORDER
    }

    default_grade_count = grade_counts[DEFAULT_GRADE_FILTER]

    grade_buttons = "".join(
        _filter_button(
            "grade",
            grade,
            GRADE_LABELS[grade],
            grade_counts[grade],
            active=grade == DEFAULT_GRADE_FILTER,
            extra_class=f"grade-filter-btn grade-filter-{grade}",
        )
        for grade in ["red", "yellow", "green"]
    )
    if grade_counts["none"]:
        grade_buttons += _filter_button(
            "grade",
            "none",
            "Unknown",
            grade_counts["none"],
            extra_class="grade-filter-btn grade-filter-none",
        )

    stage_buttons = _filter_button("stage", "all", "All Stages", len(included), active=True)
    stage_buttons += "".join(
        _filter_button("stage", stage, stage, stage_counts[stage])
        for stage in STAGE_ORDER
    )
    filters = f"""
  <div class="filters" aria-label="Report filters">
    <div class="filter-row grade-filter-row" role="group" aria-label="Filter by red, yellow, or green report status">
      <span class="filter-label grade-filter-label">Report</span>
      {grade_buttons}
      <span class="filter-status" data-filter-status>Showing {default_grade_count} of {len(included)}</span>
    </div>
    <div class="filter-row" role="group" aria-label="Filter by stage">
      <span class="filter-label">Stage</span>
      {stage_buttons}
    </div>
  </div>"""

    chips = "".join(
        f'<button type="button" class="chip chip-filter" data-filter-group="type" '
        f'data-filter-value="{_h(t)}" aria-pressed="false"'
        f'{" hidden disabled" if default_type_counts[t] == 0 else ""}>'
        f'<strong>{default_type_counts[t]}</strong>{_h(t)}</button>'
        for t in JOB_TYPE_ORDER
        if total_type_counts[t]
    )
    chips += (
        f'<button type="button" class="chip chip-filter is-active" '
        f'data-filter-group="type" data-filter-value="all" aria-pressed="true">'
        f'<strong>{default_grade_count}</strong>Total</button>'
    )
    sections_html = ""
    for grade in GRADE_ORDER:
        if grade not in grade_groups:
            continue
        cards = "".join(_card_html(p) for p in grade_groups[grade])
        label = GRADE_LABELS.get(grade, "Unknown")
        heading_class = f" stage-heading-{_h(grade)}"
        hidden = "" if grade == DEFAULT_GRADE_FILTER else " hidden"
        sections_html += f"""
  <div class="stage-section report-grade-section" data-grade-section="{_h(grade)}"{hidden}>
    <h2 class="stage-heading{heading_class}">{_h(label)} Report <span class="stage-count">{len(grade_groups[grade])}</span></h2>
    {cards}
  </div>"""

    empty_hidden = " hidden" if default_grade_count else ""
    style = str(style)
    style_css = _style_css(style)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CeriDry Project Report</title>
  <style>
    :root {{
      --bg: #eef2f7;
      --paper: #ffffff;
      --paper-soft: #f8fafc;
      --ink: #172033;
      --muted: #667085;
      --line: #d9e2ec;
      --blue: #2563eb;
      --cyan: #0891b2;
      --violet: #7c3aed;
      --green: #16a34a;
      --amber: #d97706;
      --red: #dc2626;
      --shadow: 0 10px 28px rgba(15, 23, 42, 0.10);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ background: var(--bg); }}
    body {{
      min-width: 320px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 14px;
      line-height: 1.35;
      background: var(--bg);
      color: var(--ink);
      padding: 24px;
    }}
    .report-header {{
      max-width: 1280px;
      margin: 0 auto 16px;
      padding: 18px 20px;
      text-align: left;
      background: linear-gradient(135deg, #0f172a, #1d4ed8 56%, #0891b2);
      color: #fff;
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .report-header h1 {{ font-size: 28px; line-height: 1.1; font-weight: 800; }}
    .report-header .subtitle {{ margin-top: 6px; color: #dbeafe; font-size: 13px; }}
    .summary {{
      position: sticky;
      top: 0;
      z-index: 10;
      max-width: 1280px;
      margin: 0 auto 20px;
      padding: 10px 0;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
      gap: 10px;
      background: color-mix(in srgb, var(--bg) 88%, transparent);
      backdrop-filter: blur(8px);
    }}
    .filters {{
      max-width: 1280px;
      margin: 0 auto 10px;
      padding: 12px;
      display: grid;
      gap: 10px;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
    }}
    .filter-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .filter-label {{
      width: 46px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 850;
      text-transform: uppercase;
    }}
    .filter-btn {{
      min-height: 34px;
      padding: 7px 10px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      appearance: none;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      cursor: pointer;
      font: inherit;
      font-size: 12px;
      font-weight: 850;
      line-height: 1;
      text-transform: uppercase;
    }}
    .filter-btn:hover {{ border-color: var(--blue); }}
    .filter-btn.is-active {{
      border-color: #172033;
      background: #172033;
      color: #fff;
    }}
    .filter-count {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 850;
    }}
    .filter-btn.is-active .filter-count {{ color: #dbeafe; }}
    .filter-status {{
      margin-left: auto;
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
    }}
    .grade-filter-row {{
      padding-bottom: 10px;
      border-bottom: 1px solid var(--line);
    }}
    .grade-filter-label {{ width: 70px; }}
    .grade-filter-btn {{
      min-height: 54px;
      padding: 12px 16px;
      border-width: 2px;
      font-size: 16px;
      letter-spacing: 0;
      box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
    }}
    .grade-filter-red {{
      border-color: #fecaca;
      background: #fff1f2;
      color: var(--red);
    }}
    .grade-filter-yellow {{
      border-color: #fde68a;
      background: #fffbeb;
      color: var(--amber);
    }}
    .grade-filter-green {{
      border-color: #bbf7d0;
      background: #f0fdf4;
      color: var(--green);
    }}
    .grade-filter-none {{
      border-color: #cbd5e1;
      background: #f8fafc;
      color: #475467;
    }}
    .grade-filter-red.is-active {{
      border-color: var(--red);
      background: var(--red);
      color: #fff;
    }}
    .grade-filter-yellow.is-active {{
      border-color: var(--amber);
      background: var(--amber);
      color: #fff;
    }}
    .grade-filter-green.is-active {{
      border-color: var(--green);
      background: var(--green);
      color: #fff;
    }}
    .grade-filter-none.is-active {{
      border-color: #475467;
      background: #475467;
      color: #fff;
    }}
    .grade-filter-btn.is-active .filter-count {{ color: rgba(255, 255, 255, 0.86); }}
    .chip {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-top: 4px solid var(--blue);
      border-radius: 8px;
      padding: 9px 12px;
      min-width: 0;
      box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }}
    button.chip {{
      appearance: none;
      text-align: left;
      cursor: pointer;
      font: inherit;
    }}
    button.chip:hover {{ border-color: var(--blue); }}
    .chip-filter.is-active {{
      border-color: #172033;
      border-top-color: #172033;
      background: #172033;
      color: #fff;
    }}
    .chip strong {{ display: block; font-size: 26px; line-height: 1; font-weight: 850; color: var(--ink); }}
    .chip-filter.is-active strong {{ color: #fff; }}
    .stage-section {{
      max-width: 1280px;
      margin: 0 auto 22px;
    }}
    .stage-heading {{
      position: sticky;
      top: 78px;
      z-index: 9;
      margin-bottom: 10px;
      padding: 9px 12px;
      display: flex;
      align-items: center;
      gap: 8px;
      background: #172033;
      color: #fff;
      border-radius: 8px;
      box-shadow: 0 6px 18px rgba(15, 23, 42, 0.10);
      font-size: 13px;
      font-weight: 850;
      text-transform: uppercase;
    }}
    .stage-heading-red {{ background: var(--red); }}
    .stage-heading-yellow {{ background: var(--amber); }}
    .stage-heading-green {{ background: var(--green); }}
    .stage-heading-none {{ background: #475467; }}
    .stage-count {{
      min-width: 28px;
      text-align: center;
      background: #dbeafe;
      color: #1e40af;
      font-size: 12px;
      font-weight: 850;
      border-radius: 999px;
      padding: 2px 8px;
    }}
    .card {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-left: 6px solid var(--blue);
      border-radius: 8px;
      box-shadow: var(--shadow);
      margin-bottom: 12px;
      overflow: hidden;
      break-inside: avoid;
    }}
    .card:hover {{ box-shadow: 0 14px 32px rgba(15, 23, 42, 0.14); }}
    .grade-card-red .card-header {{ background: #fff1f2; }}
    .grade-card-yellow .card-header {{ background: #fffbeb; }}
    .grade-card-green .card-header {{ background: #f0fdf4; }}
    .card-header {{
      padding: 10px 14px;
      background: #f1f5f9;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .job-num {{ font-size: 17px; line-height: 1.1; font-weight: 850; }}
    .grade-pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 4px 9px;
      border-radius: 999px;
      color: #fff;
      font-size: 11px;
      font-weight: 900;
      line-height: 1;
      text-transform: uppercase;
    }}
    .grade-pill-red {{ background: var(--red); }}
    .grade-pill-yellow {{ background: var(--amber); }}
    .grade-pill-green {{ background: var(--green); }}
    .grade-pill-none {{ background: #64748b; }}
    .meta {{
      color: #344054;
      font-size: 13px;
      font-weight: 650;
    }}
    .card-body {{
      padding: 12px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);
      gap: 12px;
      align-items: start;
    }}
    .details-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(210px, 1fr));
      gap: 10px;
      min-width: 0;
    }}
    .detail-section {{
      min-width: 0;
      background: var(--paper-soft);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    .section-label {{
      padding: 6px 8px;
      background: #dbeafe;
      color: #1e3a8a;
      border-bottom: 1px solid #bfdbfe;
      font-size: 11px;
      line-height: 1;
      font-weight: 850;
      text-transform: uppercase;
    }}
    .detail-section:nth-child(2) .section-label {{ background: #cffafe; color: #155e75; border-bottom-color: #a5f3fc; }}
    .detail-section:nth-child(3) .section-label {{ background: #ede9fe; color: #5b21b6; border-bottom-color: #ddd6fe; }}
    .detail-section:nth-child(4) .section-label {{ background: #dcfce7; color: #166534; border-bottom-color: #bbf7d0; }}
    .audit-col {{ min-width: 0; }}
    .audit-label {{
      margin: 0 0 6px;
      border-radius: 8px 8px 0 0;
      border-bottom: 1px solid currentColor;
      background: #fff;
    }}
    .info-table {{
      border-collapse: collapse;
      width: 100%;
      table-layout: fixed;
    }}
    .info-table tr + tr {{ border-top: 1px solid #e8eef5; }}
    .info-table td {{
      padding: 6px 8px;
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    .info-table td.lbl {{
      width: 43%;
      color: #475467;
      font-size: 12px;
      font-weight: 800;
    }}
    .audit-block {{
      margin-bottom: 10px;
      background: var(--paper-soft);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    .audit-list {{
      margin: 0;
      padding: 7px 10px 8px 24px;
    }}
    .audit-list li {{
      padding: 2px 0;
      color: #344054;
      font-size: 12px;
      line-height: 1.45;
    }}
    .missing-list li {{ color: var(--red); font-weight: 750; }}
    .missing-value {{ color: var(--red); font-weight: 850; }}
    .status-ok {{ color: var(--green); font-size: 16px; font-weight: 850; }}
    .status-no {{ color: var(--red); font-size: 16px; font-weight: 850; }}
    .grade-badge {{
      display: inline-block;
      margin-left: 6px;
      padding: 2px 7px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 850;
      vertical-align: middle;
    }}
    .grade-red {{ background: #fee2e2; color: var(--red); }}
    .grade-yellow {{ background: #fef3c7; color: var(--amber); }}
    .grade-green {{ background: #dcfce7; color: var(--green); }}
    .no-results {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 48px;
      text-align: center;
      color: var(--muted);
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    @media (max-width: 1120px) {{
      .card-body {{ grid-template-columns: 1fr; }}
      .details-grid {{ grid-template-columns: repeat(2, minmax(230px, 1fr)); }}
      .stage-heading {{ top: 92px; }}
    }}
    @media (max-width: 720px) {{
      body {{ padding: 12px; font-size: 13px; }}
      .report-header {{ padding: 14px; }}
      .report-header h1 {{ font-size: 23px; }}
      .filters {{ padding: 10px; }}
      .filter-label {{ width: 100%; }}
      .filter-status {{ width: 100%; margin-left: 0; }}
      .grade-filter-btn {{ flex: 1 1 calc(50% - 8px); justify-content: space-between; min-height: 48px; font-size: 14px; }}
      .summary {{ position: static; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .stage-heading {{ position: static; }}
      .card-header {{ display: block; }}
      .meta {{ display: block; margin-top: 5px; }}
      .details-grid {{ grid-template-columns: 1fr; }}
      .info-table td.lbl {{ width: 40%; }}
    }}
    @media print {{
      html, body {{ background: #fff; padding: 0; color: #000; }}
      .report-header, .summary, .stage-heading, .card, .detail-section, .audit-block {{
        box-shadow: none;
      }}
      .report-header {{ color: #000; background: #fff; border: 1px solid #999; }}
      .report-header .subtitle {{ color: #333; }}
      .summary, .stage-heading {{ position: static; backdrop-filter: none; }}
      .stage-heading {{ color: #000; background: #eee; }}
      .filters {{ display: none; }}
      .card {{ page-break-inside: avoid; break-inside: avoid; margin-bottom: 8px; }}
      .chip, .detail-section, .audit-block {{ border-color: #999; }}
    }}
{style_css}
  </style>
</head>
<body data-style="{_h(style)}">
  <div class="report-header">
    <h1>CeriDry Project Report</h1>
    <div class="subtitle">Generated {generated} &nbsp;·&nbsp; Configured job types &nbsp;·&nbsp; Active jobs from March 27, 2025 onward</div>
  </div>
  {filters if included else ""}
  <div class="summary">{chips}</div>
  {"".join(sections_html) if included else '<div class="no-results">No matching projects found.</div>'}
  {f'<div class="no-results" data-filter-empty{empty_hidden}>No projects match those filters.</div>' if included else ""}
  <script>
    (() => {{
      const state = {{ grade: "{DEFAULT_GRADE_FILTER}", type: "all", stage: "all" }};
      const buttons = Array.from(document.querySelectorAll("[data-filter-group]"));
      const typeButtons = buttons.filter((button) => button.dataset.filterGroup === "type");
      const cards = Array.from(document.querySelectorAll(".card"));
      const sections = Array.from(document.querySelectorAll(".stage-section"));
      const status = document.querySelector("[data-filter-status]");
      const empty = document.querySelector("[data-filter-empty]");
      const total = cards.length;

      const matches = (value, selected) => selected === "all" || value === selected;

      function setActive(group, value) {{
        state[group] = value;
        buttons
          .filter((button) => button.dataset.filterGroup === group)
          .forEach((button) => {{
            const active = button.dataset.filterValue === value;
            button.classList.toggle("is-active", active);
            button.setAttribute("aria-pressed", active ? "true" : "false");
          }});
      }}

      function scopedTypeCount(typeValue) {{
        let count = 0;
        cards.forEach((card) => {{
          const inScope = (
            matches(card.dataset.grade || "", state.grade) &&
            matches(card.dataset.stage || "", state.stage) &&
            (typeValue === "all" || card.dataset.jobType === typeValue)
          );
          if (inScope) {{
            count += 1;
          }}
        }});
        return count;
      }}

      function updateTypeChips() {{
        let selectedStillAvailable = state.type === "all";

        typeButtons.forEach((button) => {{
          const value = button.dataset.filterValue || "all";
          const count = scopedTypeCount(value);
          const countElement = button.querySelector("strong");
          const unavailable = value !== "all" && count === 0;

          if (countElement) {{
            countElement.textContent = String(count);
          }}
          button.hidden = unavailable;
          button.disabled = unavailable;

          if (value === state.type && !unavailable) {{
            selectedStillAvailable = true;
          }}
        }});

        if (!selectedStillAvailable) {{
          setActive("type", "all");
        }}
      }}

      function applyFilters() {{
        updateTypeChips();
        let visibleTotal = 0;

        sections.forEach((section) => {{
          let visibleInStage = 0;

          section.querySelectorAll(".card").forEach((card) => {{
            const visible = (
              matches(card.dataset.grade || "", state.grade) &&
              matches(card.dataset.jobType || "", state.type) &&
              matches(card.dataset.stage || "", state.stage)
            );
            card.hidden = !visible;
            if (visible) {{
              visibleInStage += 1;
            }}
          }});

          section.hidden = visibleInStage === 0;
          const count = section.querySelector(".stage-count");
          if (count) {{
            count.textContent = String(visibleInStage);
          }}
          visibleTotal += visibleInStage;
        }});

        if (status) {{
          status.textContent = `Showing ${{visibleTotal}} of ${{total}}`;
        }}
        if (empty) {{
          empty.hidden = visibleTotal > 0;
        }}
      }}

      buttons.forEach((button) => {{
        button.addEventListener("click", () => {{
          setActive(button.dataset.filterGroup, button.dataset.filterValue || "all");
          applyFilters();
        }});
      }});

      applyFilters();
    }})();
  </script>
</body>
</html>"""

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.set_defaults(style="1")
    parser.add_argument("--api", action="store_true",
                        help="Pull live data from Albi API instead of local saved/ files")
    parser.add_argument("--out", default=datetime.now().strftime("%m%d%y") + "Report.html",
                        help="Output filename (default: mmddyyReport.html)")
    parser.add_argument("--style", choices=sorted(STYLE_LABELS),
                        help="Visual style number: 1 clean, 2 dark, 3 audit, 4 tech")
    parser.add_argument("-1", action="store_const", const="1", dest="style",
                        help="Style 1: clean minimal / corporate SaaS")
    parser.add_argument("-2", action="store_const", const="2", dest="style",
                        help="Style 2: executive dark / Linear-like")
    parser.add_argument("-3", action="store_const", const="3", dest="style",
                        help="Style 3: government audit / newspaper")
    parser.add_argument("-4", action="store_const", const="4", dest="style",
                        help="Style 4: tech blueprint / terminal neon")
    args = parser.parse_args()

    if args.api:
        print("Fetching projects from Albi API...", flush=True)
        projects = _load_api()
    else:
        print("Loading projects from local files...", flush=True)
        projects = _load_local()

    included = [p for p in projects if _is_included(p)]
    print(
        f"  {len(projects)} total → {len(included)} matching active jobs "
        f"({len(INCLUDED_TYPES)} configured types, after Mar 27 2025)",
        flush=True,
    )

    reports_dir = os.path.join(_BASE, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    out_path = os.path.join(reports_dir, args.out)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(_build_html(projects, style=args.style))

    print(f"  Report written to: {out_path}", flush=True)
    print(f"  Style: {args.style} ({STYLE_LABELS[args.style]})", flush=True)
    print(f"  Open with: open \"{out_path}\"", flush=True)


if __name__ == "__main__":
    main()
