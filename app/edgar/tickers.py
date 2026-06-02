"""Resolve a stock ticker to its SEC CIK.

Uses the SEC's published ticker->CIK map. The map is fetched once and cached
in memory for the lifetime of the process.
"""

from __future__ import annotations

import asyncio

from app.edgar.client import EdgarClient, EdgarError

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


class TickerResolver:
    """Caches the SEC ticker map and resolves tickers to CIK + company name."""

    def __init__(self, client: EdgarClient) -> None:
        self._client = client
        self._map: dict[str, tuple[str, str]] | None = None
        self._lock = asyncio.Lock()

    async def _ensure_loaded(self) -> dict[str, tuple[str, str]]:
        if self._map is not None:
            return self._map
        async with self._lock:
            if self._map is not None:
                return self._map
            raw = await self._client.get_json(COMPANY_TICKERS_URL)
            mapping: dict[str, tuple[str, str]] = {}
            # The payload is a dict keyed by row index; each value has
            # cik_str, ticker, and title.
            for entry in raw.values():
                ticker = str(entry["ticker"]).upper()
                cik = f"{int(entry['cik_str']):010d}"
                title = str(entry.get("title", ""))
                mapping[ticker] = (cik, title)
            self._map = mapping
            return self._map

    async def resolve(self, ticker: str) -> tuple[str, str]:
        """Return ``(cik, company_name)`` for ``ticker``.

        Class-share tickers are commonly written with a dot (``BRK.B``), but the
        SEC's map uses a dash (``BRK-B``). We try the input as given, then with
        ``.`` normalized to ``-``, so both styles resolve.

        Raises ``EdgarError`` (with a clear message) if the ticker is unknown.
        """

        mapping = await self._ensure_loaded()
        base = ticker.strip().upper()
        for key in (base, base.replace(".", "-")):
            if key in mapping:
                return mapping[key]
        raise EdgarError(f"Unknown ticker: {ticker!r}")
