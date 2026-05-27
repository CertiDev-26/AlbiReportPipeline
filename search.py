import os
import time

import requests
from dotenv import load_dotenv


load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

BASE_URL = "https://api.albiware.com/v5/Integrations"

COMPANIES = [
    ("CeriDry", os.getenv("CertiDry_API_KEY")),
    ("360 Pros", os.getenv("360Pros_API_KEY")),
    ("One Call", os.getenv("oneCall_API_KEY")),
    ("Statewide Construction", os.getenv("statewideConstruction_API_KEY")),
    ("United Commercial Services", os.getenv("unitedCommercialServices_API_KEY")),
    ("United Drywall & Painting", os.getenv("unitedDrywallPainting_API_KEY")),
]


def get(endpoint, api_key, params=None, _retries=3):
    url = f"{BASE_URL}/{endpoint}"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    for attempt in range(_retries):
        response = requests.get(url, headers=headers, params=params, timeout=60)
        if response.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        if not response.ok:
            raise RuntimeError(f"Albi API error {response.status_code} for {url}: {response.text}")
        return response.json()
    raise RuntimeError(f"Albi API rate limited after {_retries} retries for {url}")


def _get_all_paged(endpoint, api_key):
    records = []
    page = 1
    while True:
        data = get(endpoint, api_key, params={"page": page, "pageSize": 100})
        batch = data if isinstance(data, list) else (data.get("data", []) if isinstance(data, dict) else [])
        if not batch:
            break
        records.extend(batch)
        pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
        if pagination:
            if page >= pagination.get("totalPages", 1):
                break
        elif len(batch) < 100:
            break
        page += 1
    return records


def get_all_projects(api_key):
    return _get_all_paged("Projects", api_key)


def get_all_contacts(api_key):
    return _get_all_paged("Contacts", api_key)
