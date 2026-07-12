import datetime
from unittest.mock import MagicMock, patch

import pytest

from slimmemeterportal_client import (
    AuthenticationError,
    BadRequestError,
    Connection,
    RateLimitError,
    SlimmeMeterPortalClient,
    find_missing_dates,
    flatten_usage_range,
)


def make_response(status_code=200, json_data=None, headers=None):
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = json_data
    if status_code >= 400:
        response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        response.raise_for_status.return_value = None
    return response


def make_client():
    return SlimmeMeterPortalClient(api_key="test-key")


def test_requires_api_key():
    with pytest.raises(ValueError):
        SlimmeMeterPortalClient(api_key="")


def test_get_connections_sends_api_key_header():
    client = make_client()
    data = [
        {
            "meter_identifier": "E0012345678901234567",
            "connection_type": "electricity",
            "start_date": "2024-01-01",
            "end_date": None,
        }
    ]
    with patch.object(client.session, "get", return_value=make_response(200, data)) as mock_get:
        connections = client.get_connections()

    mock_get.assert_called_once()
    called_headers = mock_get.call_args.kwargs["headers"]
    assert called_headers == {"API-Key": "test-key"}
    assert connections == [
        Connection(
            meter_identifier="E0012345678901234567",
            connection_type="electricity",
            start_date="2024-01-01",
            end_date=None,
        )
    ]


def test_get_connections_missing_end_date_key():
    client = make_client()
    data = [{"meter_identifier": "M1", "connection_type": "electricity", "start_date": "2024-01-01"}]
    with patch.object(client.session, "get", return_value=make_response(200, data)):
        connections = client.get_connections()

    assert connections[0].end_date is None


def test_get_usage_accepts_date_object():
    client = make_client()
    payload = {"meter_identifier": "M1", "usages": []}
    with patch.object(client.session, "get", return_value=make_response(200, payload)) as mock_get:
        result = client.get_usage("M1", datetime.date(2024, 1, 15))

    assert result == payload
    called_url = mock_get.call_args.args[0]
    # The API expects dd-mm-YYYY, not ISO format.
    assert called_url.endswith("/connections/M1/usage/15-01-2024")


def test_get_usage_accepts_date_string():
    client = make_client()
    payload = {"meter_identifier": "M1", "usages": []}
    with patch.object(client.session, "get", return_value=make_response(200, payload)) as mock_get:
        client.get_usage("M1", "15-01-2024")

    called_url = mock_get.call_args.args[0]
    assert called_url.endswith("/connections/M1/usage/15-01-2024")


def test_403_raises_authentication_error():
    client = make_client()
    with patch.object(
        client.session, "get", return_value=make_response(403, {"error": "Invalid API key"})
    ):
        with pytest.raises(AuthenticationError, match="Invalid API key"):
            client.get_connections()


def test_400_raises_bad_request_error():
    client = make_client()
    with patch.object(
        client.session, "get", return_value=make_response(400, {"error": "Invalid date"})
    ):
        with pytest.raises(BadRequestError, match="Invalid date"):
            client.get_usage("M1", "not-a-date")


def test_429_raises_rate_limit_error_with_retry_after():
    client = make_client()
    with patch.object(
        client.session, "get", return_value=make_response(429, headers={"Retry-After": "12"})
    ):
        with pytest.raises(RateLimitError) as exc_info:
            client.get_connections()

    assert exc_info.value.retry_after == 12.0


def test_429_without_retry_after_header():
    client = make_client()
    with patch.object(client.session, "get", return_value=make_response(429)):
        with pytest.raises(RateLimitError) as exc_info:
            client.get_connections()

    assert exc_info.value.retry_after is None


def test_get_usage_range_retries_after_rate_limit(monkeypatch):
    client = make_client()
    payload = {"meter_identifier": "M1", "usages": []}
    responses = [
        make_response(429, headers={"Retry-After": "0.01"}),
        make_response(200, payload),
    ]
    sleep_calls = []
    monkeypatch.setattr("slimmemeterportal_client.time.sleep", lambda s: sleep_calls.append(s))

    with patch.object(client.session, "get", side_effect=responses):
        results = client.get_usage_range("M1", ["2024-01-01"])

    assert results == [payload]
    assert sleep_calls == [0.01]


def test_get_usage_range_treats_bad_request_as_no_data_and_continues():
    client = make_client()
    ok_payload = {"meter_identifier": "M1", "usages": [{"time": "02-01-2024 00:00:00 +0100"}]}
    responses = [
        make_response(400, {"error": "Date out of range"}),  # 2024-01-01: before coverage
        make_response(200, ok_payload),  # 2024-01-02: has data
    ]

    with patch.object(client.session, "get", side_effect=responses):
        results = client.get_usage_range("M1", ["2024-01-01", "2024-01-02"])

    assert results == [{"usages": []}, ok_payload]


def test_find_missing_dates():
    dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
    day_responses = [
        {"usages": []},
        {"meter_identifier": "M1", "usages": [{"time": "02-01-2024 00:00:00 +0100"}]},
        {"meter_identifier": "M1", "usages": []},
    ]

    assert find_missing_dates(dates, day_responses) == ["2024-01-01", "2024-01-03"]


def test_find_missing_dates_none_missing():
    dates = ["2024-01-01"]
    day_responses = [{"usages": [{"time": "01-01-2024 00:00:00 +0100"}]}]

    assert find_missing_dates(dates, day_responses) == []


def test_flatten_usage_range_ignores_days_with_no_usages():
    day_responses = [
        {"usages": []},
        {"usages": [{"time": "a"}, {"time": "b"}]},
    ]

    assert flatten_usage_range(day_responses) == [{"time": "a"}, {"time": "b"}]
