# Albi Report Pipeline

Standalone HTML job report generator for CeriDry/Albi project data.

## Files

- `report.py` generates `report.html`.
- `search.py` is the minimal Albi API helper used by `report.py --api`.
- `ReportIfo.md` stores report design preferences.
- `prompt.md` and `ALIBIREPORT.md` preserve report-format notes.
- `saved/` contains local `project.json` and `inspect_files.json` metadata only. Videos, transcripts, and automation state were intentionally not copied.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For live API reports, create `.env` from `.env.example` and fill in the Albi API keys.

```sh
cp .env.example .env
```

## Run

Generate from local saved metadata:

```sh
python3 report.py
```

Generate from live Albi API data:

```sh
python3 report.py --api --out report.html
```

Choose a visual style:

```sh
python3 report.py -1  # clean minimal
python3 report.py -2  # executive dark
python3 report.py -3  # government/audit
python3 report.py -4  # tech blueprint
```

Open the output:

```sh
open report.html
```
