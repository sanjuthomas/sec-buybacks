# SEC Buyback Detector API

A small Python (FastAPI) service that, given a stock ticker, finds share
buyback / stock repurchase announcements a company has made to the SEC in the
last 365 days.

Given a ticker it:

1. Resolves the ticker to its SEC CIK.
2. Pulls the company's recent `10-K`, `10-Q`, and `8-K` filings from EDGAR.
3. Keeps only filings filed within the last 365 days.
4. Scans each filing's primary document for buyback-related phrases:
   - `share repurchase program`
   - `stock buyback authorization`
   - `board authorized repurchase`
5. For each match, returns the filing metadata, a context snippet, and a
   best-effort parsed authorization amount.

## Requirements

- Python 3.11+ (developed against 3.13)

## Setup

```bash
cd sec-buybacks
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The SEC requires a descriptive `User-Agent` for all programmatic requests.
Set yours via an environment variable (recommended):

```bash
export SEC_USER_AGENT="Your Name your.email@example.com"
```

If unset, a default placeholder is used, but you should provide a real contact
to avoid being throttled or blocked by the SEC.

## Run

```bash
uvicorn app.main:app --port 8080
```

Then:

```bash
curl http://localhost:8080/api/buybacks/ADBE
```

Interactive API docs are available at http://localhost:8080/docs

## Example response

```json
{
  "ticker": "ADBE",
  "cik": "0000796343",
  "company_name": "ADOBE INC.",
  "lookback_days": 365,
  "count": 1,
  "announcements": [
    {
      "announcement_date": "2025-03-12",
      "report_date": "2025-03-11",
      "authorization_amount": 25000000000.0,
      "authorization_amount_text": "$25 billion",
      "amount_context": "...the board authorized a new $25 billion stock repurchase program...",
      "matched_token": "share repurchase program",
      "form": "8-K",
      "filing_date": "2025-03-12",
      "filing_url": "https://www.sec.gov/Archives/edgar/data/796343/.../adbe-8k.htm"
    }
  ]
}
```

## Tests

```bash
pytest
```

## Notes

- Only the primary document of each filing is scanned initially. Scanning all
  exhibits can be added later.
- `announcement_date` defaults to the SEC filing date; `report_date` (the
  filing's period of report, e.g. for an 8-K) is included when available.
- Amount parsing is best-effort. `authorization_amount` is `null` when no
  dollar figure can be confidently associated with a match.
