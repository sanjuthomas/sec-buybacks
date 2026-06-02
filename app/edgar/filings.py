"""Fetch a company's filings from EDGAR and filter by form + date window."""

from __future__ import annotations

from datetime import date, timedelta

from app.config import settings
from app.edgar.client import EdgarClient
from app.models import Filing

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{doc}"


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _build_document_url(cik: str, accession_number: str, primary_document: str) -> str:
    accession_nodashes = accession_number.replace("-", "")
    return ARCHIVES_URL.format(
        cik_int=int(cik),
        accession=accession_nodashes,
        doc=primary_document,
    )


async def fetch_recent_filings(
    client: EdgarClient,
    cik: str,
    *,
    forms: tuple[str, ...] | None = None,
    lookback_days: int | None = None,
    today: date | None = None,
) -> list[Filing]:
    """Return filings of the requested forms filed within the lookback window.

    ``cik`` must be the zero-padded 10-digit CIK string.
    """

    forms = forms or settings.forms
    lookback_days = lookback_days or settings.lookback_days
    today = today or date.today()
    cutoff = today - timedelta(days=lookback_days)
    wanted_forms = {f.upper() for f in forms}

    payload = await client.get_json(SUBMISSIONS_URL.format(cik=cik))
    recent = payload.get("filings", {}).get("recent", {})

    form_list = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])

    results: list[Filing] = []
    for i, form in enumerate(form_list):
        if form.upper() not in wanted_forms:
            continue
        filing_date = _parse_date(filing_dates[i]) if i < len(filing_dates) else None
        if filing_date is None or filing_date < cutoff:
            continue
        primary_document = (
            primary_documents[i] if i < len(primary_documents) else ""
        )
        if not primary_document:
            continue
        accession_number = accession_numbers[i]
        report_date = (
            _parse_date(report_dates[i]) if i < len(report_dates) else None
        )
        results.append(
            Filing(
                form=form,
                filing_date=filing_date,
                report_date=report_date,
                accession_number=accession_number,
                primary_document=primary_document,
                document_url=_build_document_url(
                    cik, accession_number, primary_document
                ),
            )
        )
    return results
