"""Pydantic models for API responses and internal data passing."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class Filing(BaseModel):
    """A single SEC filing selected for scanning."""

    form: str
    filing_date: date
    report_date: date | None = None
    accession_number: str
    primary_document: str
    document_url: str


class BuybackAnnouncement(BaseModel):
    """One buyback-related match found within a filing."""

    event_type: str = Field(
        ...,
        description=(
            "'new_authorization' if the filing announces a new/expanded "
            "buyback authorization, or 'reference' if it merely refers to an "
            "existing program (e.g. quarterly execution disclosure)."
        ),
    )
    announcement_date: date = Field(
        ...,
        description=(
            "The board authorization date when detected, otherwise the SEC "
            "filing date."
        ),
    )
    authorization_date: date | None = Field(
        None,
        description="Board authorization date parsed from the filing text.",
    )
    report_date: date | None = Field(
        None, description="Filing period of report, when provided by EDGAR."
    )
    authorization_amount: float | None = Field(
        None, description="Parsed authorization amount in USD, or null."
    )
    authorization_amount_text: str | None = Field(
        None, description="Raw amount text as it appeared in the filing."
    )
    amount_context: str = Field(
        ..., description="Snippet of surrounding text around the match."
    )
    matched_token: str
    form: str
    filing_date: date
    filing_url: str


class BuybackResponse(BaseModel):
    """Top-level API response for a ticker."""

    ticker: str
    cik: str
    company_name: str | None = None
    lookback_days: int
    count: int = Field(..., description="Number of announcements returned.")
    new_authorization_count: int = Field(
        ..., description="Distinct new authorizations detected in the window."
    )
    reference_count: int = Field(
        ..., description="Reference/execution mentions detected in the window."
    )
    announcements: list[BuybackAnnouncement]
