"""Exceptions for screener_finance."""


class ScreenerError(RuntimeError):
    """Base error."""


class CompanyNotFoundError(ScreenerError):
    """Symbol does not exist on Screener.in (404)."""


class RateLimitError(ScreenerError):
    """HTTP 429 after retries."""

    def __init__(self, retry_after: int | None = None):
        super().__init__(f"Rate limited by screener.in (Retry-After={retry_after}). "
                         f"Increase the delay between requests.")
        self.retry_after = retry_after


class ViewUnavailableError(ScreenerError):
    """Neither consolidated nor standalone view exists for the company."""

    def __init__(self, symbol: str):
        super().__init__(f"No data view available for {symbol!r}")
        self.symbol = symbol
