"""Tests for per-document EDGAR scan caching."""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.analysis.extractor import EVENT_REFERENCE
from app.db.document_store import DocumentStore
from app.edgar.client import EdgarError
from app.main import _scan_document
from app.models import BuybackAnnouncement, Filing

DOCUMENT_URL = (
    "https://www.sec.gov/Archives/edgar/data/1234567/"
    "000123456726000001/adbe-20260331.htm"
)
TICKER = "ADBE"
CIK = "0000796343"


def _filing() -> Filing:
    return Filing(
        form="8-K",
        filing_date=date(2026, 4, 21),
        report_date=date(2026, 4, 15),
        accession_number="0001234567-26-000001",
        primary_document="adbe-20260331.htm",
        document_url=DOCUMENT_URL,
    )


def _cached_announcement() -> BuybackAnnouncement:
    return BuybackAnnouncement(
        event_type=EVENT_REFERENCE,
        announcement_date=date(2026, 4, 21),
        report_date=date(2026, 4, 15),
        amount_context="repurchase program under the plan",
        matched_token="repurchase program",
        form="8-K",
        filing_date=date(2026, 4, 21),
        filing_url=DOCUMENT_URL,
    )


def test_scan_document_returns_cache_without_download():
    client = AsyncMock()
    cached = [_cached_announcement()]

    async def run():
        with patch("app.main.document_store.get", AsyncMock(return_value=cached)):
            return await _scan_document(
                client, _filing(), DOCUMENT_URL, ticker=TICKER, cik=CIK
            )

    result = asyncio.run(run())
    assert result == cached
    client.get_text.assert_not_called()


def test_scan_document_downloads_and_caches_on_miss():
    client = AsyncMock()
    client.get_text.return_value = (
        "<html><body>We announced a new stock repurchase program.</body></html>"
    )
    mock_get = AsyncMock(return_value=None)
    mock_put = AsyncMock()

    async def run():
        with (
            patch("app.main.document_store.get", mock_get),
            patch("app.main.document_store.put", mock_put),
        ):
            return await _scan_document(
                client, _filing(), DOCUMENT_URL, ticker=TICKER, cik=CIK
            )

    result = asyncio.run(run())
    client.get_text.assert_awaited_once_with(DOCUMENT_URL)
    mock_put.assert_awaited_once()
    assert mock_put.await_args.args[0] == DOCUMENT_URL
    assert mock_put.await_args.args[1] == result
    assert mock_put.await_args.kwargs == {"ticker": TICKER, "cik": CIK}
    assert len(result) >= 1


def test_scan_document_caches_empty_results():
    client = AsyncMock()
    client.get_text.return_value = "<html><body>No buybacks here.</body></html>"
    mock_put = AsyncMock()

    async def run():
        with (
            patch("app.main.document_store.get", AsyncMock(return_value=None)),
            patch("app.main.document_store.put", mock_put),
        ):
            return await _scan_document(
                client, _filing(), DOCUMENT_URL, ticker=TICKER, cik=CIK
            )

    result = asyncio.run(run())
    assert result == []
    mock_put.assert_awaited_once_with(
        DOCUMENT_URL, [], ticker=TICKER, cik=CIK
    )


def test_scan_document_does_not_cache_download_failure():
    client = AsyncMock()
    client.get_text.side_effect = EdgarError("network")
    mock_put = AsyncMock()

    async def run():
        with (
            patch("app.main.document_store.get", AsyncMock(return_value=None)),
            patch("app.main.document_store.put", mock_put),
        ):
            return await _scan_document(
                client, _filing(), DOCUMENT_URL, ticker=TICKER, cik=CIK
            )

    result = asyncio.run(run())
    assert result == []
    mock_put.assert_not_called()


def test_document_store_roundtrip_serializes_announcements():
    store = DocumentStore()
    collection = MagicMock()
    stored: dict = {}

    async def fake_find_one(query):
        return stored.get(query["_id"])

    async def fake_replace_one(query, doc, upsert=False):
        stored[query["_id"]] = doc

    collection.find_one = AsyncMock(side_effect=fake_find_one)
    collection.replace_one = AsyncMock(side_effect=fake_replace_one)
    collection.create_index = AsyncMock()
    store._collection = lambda: collection  # type: ignore[method-assign]

    announcements = [_cached_announcement()]

    async def run() -> None:
        await store.put(
            DOCUMENT_URL, announcements, ticker=TICKER, cik=CIK
        )
        loaded = await store.get(DOCUMENT_URL)

        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0].model_dump() == announcements[0].model_dump()
        doc = stored[DOCUMENT_URL]
        assert doc["ticker"] == TICKER
        assert doc["cik"] == CIK

    asyncio.run(run())


def test_document_store_creates_ticker_and_cik_indexes():
    store = DocumentStore()
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.create_index = AsyncMock()
    store._collection = lambda: collection  # type: ignore[method-assign]

    asyncio.run(store.get(DOCUMENT_URL))

    assert collection.create_index.await_count == 2
    index_names = [
        call.args[0] for call in collection.create_index.await_args_list
    ]
    assert index_names == ["ticker", "cik"]


def test_document_store_get_returns_none_on_miss():
    store = DocumentStore()
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.create_index = AsyncMock()
    store._collection = lambda: collection  # type: ignore[method-assign]

    assert asyncio.run(store.get(DOCUMENT_URL)) is None
