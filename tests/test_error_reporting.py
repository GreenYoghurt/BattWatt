import io
from unittest.mock import MagicMock, patch

import pytest

from data_loader import SmartLoader, UnexpectedValueError
from error_reporting import (
    build_alert,
    describe_upload,
    mask_value,
    report_upload_error,
    send_alert,
)

HEADER = "time,Import T1 kWh,Import T2 kWh,Export T1 kWh,Export T2 kWh,L1 max W,L2 max W,L3 max W\n"


class FakeUpload(io.BytesIO):
    """Mimics Streamlit's UploadedFile (BytesIO with .name/.size/.file_id)."""

    def __init__(self, text: str, name: str):
        data = text.encode()
        super().__init__(data)
        self.name = name
        self.size = len(data)
        self.file_id = "test"


def _unknown_format_upload(name="Fuikweg 12 meterdata.csv"):
    # A hypothetical future export: negatives written as "(30)" instead of "'-30"
    return FakeUpload(
        HEADER
        + "2025-01-02 09:30,27957.929,33283.872,2534.417,5951.063,129,30,373\n"
        + "2025-01-02 09:45,27958.104,33283.872,2534.417,5951.063,129,(30),373\n",
        name,
    )


def test_mask_value_hides_digits_and_letters():
    assert mask_value("'-30") == "'-99"
    assert mask_value("2025-01-02 09:45") == "9999-99-99 99:99"
    assert mask_value("abc12") == "xxx99"


def test_homewizard_unknown_pattern_raises_clear_error():
    with pytest.raises(UnexpectedValueError) as exc_info:
        SmartLoader.load_with_checks(_unknown_format_upload())
    err = exc_info.value
    assert err.column == "L2 max W"
    assert err.examples == ["(30)"]
    assert "exportformaat" in str(err)


def test_alert_contains_structure_but_no_data_or_file_name():
    upload = _unknown_format_upload()
    with pytest.raises(UnexpectedValueError) as exc_info:
        SmartLoader.load_with_checks(upload)

    text = build_alert(exc_info.value, describe_upload(upload), upload)

    # Useful for diagnosis
    assert "UnexpectedValueError" in text
    assert "L2 max W" in text
    assert "(99)" in text  # masked pattern of the offending value
    assert "HomeWizardLoader" in text
    # Never leaks data or the file name
    for secret in ["Fuikweg", "27957", "33283", "2025-01-02", "(30)", "129"]:
        assert secret not in text, secret


def test_alert_for_undetected_format_strips_file_name():
    upload = FakeUpload("Datum;Stand 871234567890123456\n01-01-2025;12345,6\n", "Fuikweg 12.csv")
    with pytest.raises(ValueError) as exc_info:
        SmartLoader.load_with_checks(upload)

    text = build_alert(exc_info.value, describe_upload(upload), upload)

    assert "Fuikweg" not in text
    assert "871234567890123456" not in text  # EAN-like number in a header
    assert "12345" not in text


def test_send_alert_payload_per_target():
    ok = MagicMock(ok=True)
    with patch("error_reporting.requests.post", return_value=ok) as post:
        assert send_alert("https://ntfy.sh/some-topic", "hello")
        assert post.call_args.kwargs["data"] == b"hello"

        assert send_alert("https://discord.com/api/webhooks/1/x", "hello")
        assert post.call_args.kwargs["json"] == {"content": "hello"}

        assert send_alert("https://hooks.slack.com/services/x", "hello")
        assert post.call_args.kwargs["json"] == {"text": "hello"}


def test_send_alert_never_raises():
    assert send_alert(None, "hello") is False
    with patch("error_reporting.requests.post", side_effect=ConnectionError("down")):
        assert send_alert("https://ntfy.sh/some-topic", "hello") is False


def test_report_upload_error_never_raises_on_garbage():
    with patch("error_reporting.requests.post", return_value=MagicMock(ok=True)):
        assert report_upload_error(RuntimeError("x"), FakeUpload("\x00\x01garbage", "x.xlsx"), "https://ntfy.sh/t") is True
    assert report_upload_error(RuntimeError("x"), object(), None) is False
