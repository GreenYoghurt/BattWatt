"""Client for the Slimme Meter Portal UserAPI.

API docs: https://app.slimmemeterportal.nl/api-docs/index.html
Auth: API-Key header. Rate limit: 60 requests/minute per client (429 + Retry-After on excess).
"""
import time
from dataclasses import dataclass
from datetime import date as date_type
from typing import Any, Dict, List, Optional, Union

import requests

BASE_URL = "https://app.slimmemeterportal.nl/userapi/v1"


class SlimmeMeterPortalError(Exception):
    """Base error for Slimme Meter Portal API failures."""


class AuthenticationError(SlimmeMeterPortalError):
    """Raised on a 403 response (invalid or missing API key)."""


class RateLimitError(SlimmeMeterPortalError):
    """Raised on a 429 response."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class BadRequestError(SlimmeMeterPortalError):
    """Raised on a 400 response (e.g. unknown connection_id or malformed date)."""


@dataclass
class Connection:
    meter_identifier: str
    connection_type: str
    start_date: str
    end_date: Optional[str] = None


class SlimmeMeterPortalClient:
    """Thin wrapper around the Slimme Meter Portal UserAPI.

    Translates non-2xx responses into typed exceptions. Does not retry
    automatically on rate limiting; callers get `RateLimitError.retry_after`
    and decide whether to wait, except in `get_usage_range`, which needs to
    make many sequential requests and so waits and retries by default.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        timeout: float = 10.0,
        session: Optional[requests.Session] = None,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _headers(self) -> Dict[str, str]:
        return {"API-Key": self.api_key}

    @staticmethod
    def _error_message(response: requests.Response, default: str) -> str:
        try:
            return response.json().get("error", default)
        except ValueError:
            return default

    def _request(self, path: str) -> Any:
        response = self.session.get(
            f"{self.base_url}{path}", headers=self._headers(), timeout=self.timeout
        )

        if response.status_code == 403:
            raise AuthenticationError(self._error_message(response, "Forbidden"))
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                retry_after = float(retry_after) if retry_after is not None else None
            except ValueError:
                retry_after = None
            raise RateLimitError(
                "Rate limit exceeded (max 60 requests/minute)", retry_after=retry_after
            )
        if response.status_code == 400:
            raise BadRequestError(self._error_message(response, "Bad request"))
        response.raise_for_status()
        return response.json()

    def get_connections(self) -> List[Connection]:
        """List the meter connections available to this API key."""
        data = self._request("/connections")
        return [
            Connection(
                meter_identifier=item["meter_identifier"],
                connection_type=item["connection_type"],
                start_date=item["start_date"],
                end_date=item.get("end_date"),
            )
            for item in data
        ]

    def get_usage(self, connection_id: str, date: Union[str, date_type]) -> Dict[str, Any]:
        """Fetch a single day of usage data for a connection.

        `date` accepts a `datetime.date`, which is formatted as dd-mm-YYYY
        (the API's expected format -- confirmed against the live API, since
        the OpenAPI spec only declares `type: string` with no format hint).
        A pre-formatted dd-mm-YYYY string is also accepted as-is.
        """
        if isinstance(date, date_type):
            date = date.strftime("%d-%m-%Y")
        return self._request(f"/connections/{connection_id}/usage/{date}")

    def get_usage_range(
        self,
        connection_id: str,
        dates: List[Union[str, date_type]],
        default_retry_after: float = 60.0,
        progress_callback: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch usage for multiple days, one request per date.

        The usage endpoint only accepts a single date, so covering a range
        requires one request per day. If the rate limit is hit, this waits
        for `Retry-After` (or `default_retry_after` if the header is absent)
        and retries the same date.

        `progress_callback`, if given, is called as `progress_callback(current, total)`
        after each date is fetched (`current` is 1-based).

        A `BadRequestError` for an individual date (e.g. a date outside the
        connection's coverage) is treated as "no data for that date" rather
        than aborting the whole range -- callers can spot these via
        `find_missing_dates`.
        """
        results = []
        total = len(dates)
        for i, date in enumerate(dates):
            while True:
                try:
                    results.append(self.get_usage(connection_id, date))
                    break
                except RateLimitError as e:
                    time.sleep(e.retry_after or default_retry_after)
                except BadRequestError:
                    results.append({"usages": []})
                    break
            if progress_callback:
                progress_callback(i + 1, total)
        return results


def flatten_usage_range(day_responses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten `get_usage_range` output (one dict per day) into a single list of usage records."""
    return [usage for day in day_responses for usage in day.get("usages", [])]


def find_missing_dates(
    dates: List[Union[str, date_type]], day_responses: List[Dict[str, Any]]
) -> List[Union[str, date_type]]:
    """Return the dates for which `get_usage_range` returned no usage records
    (before the connection's start date, after its end date, or a data gap)."""
    return [d for d, resp in zip(dates, day_responses) if not resp.get("usages")]
