"""Privacy-safe alerts for failed meter-data uploads.

When an upload fails in the web app, the maintainer gets a short notification
describing the *shape* of the file, so format changes by meter apps (e.g.
HomeWizard) are noticed without waiting for a user to report them.

Privacy rules (the app promises to store no data):
- Never send meter readings, timestamps, file names or file contents.
- Cell values are only sent as masked patterns: every digit becomes "9" and
  every letter "x", so "'-30" is sent as "'-99".
- Column names are sent (they identify the format), with long digit runs
  masked in case a header contains a meter/EAN number.

Configure with the ALERT_WEBHOOK_URL secret. Supported targets:
- ntfy (https://ntfy.sh/<topic>): plain-text push notification
- Discord webhook: JSON {"content": ...}
- anything else (e.g. Slack incoming webhook): JSON {"text": ...}
"""

from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

MAX_COLUMNS = 25
MAX_EXAMPLES = 3
SAMPLE_ROWS = 500
TIMEOUT_SECONDS = 5


def mask_value(value: Any, max_len: int = 20) -> str:
    """Return the pattern of a cell value with all digits and letters masked."""
    text = str(value)[:max_len]
    text = re.sub(r"[0-9]", "9", text)
    return re.sub(r"[^\W\d_]", "x", text)


def _mask_header(name: Any, max_len: int = 40) -> str:
    """Keep column names readable, but mask digit runs of 6+ (EAN/serial numbers)."""
    return re.sub(r"\d{6,}", lambda m: "9" * len(m.group()), str(name))[:max_len]


def _read_sample(upload: Any) -> Optional[pd.DataFrame]:
    name = str(getattr(upload, "name", upload)).lower()
    try:
        if hasattr(upload, "getvalue"):
            src: Any = io.BytesIO(upload.getvalue())
        else:
            if hasattr(upload, "seek"):
                upload.seek(0)
            src = upload
        if name.endswith(".csv"):
            return pd.read_csv(src, nrows=SAMPLE_ROWS, dtype=str, sep=None, engine="python")
        return pd.read_excel(src, nrows=SAMPLE_ROWS, dtype=str)
    except Exception:
        return None
    finally:
        if hasattr(upload, "seek"):
            try:
                upload.seek(0)
            except Exception:
                pass


def _detected_loader(upload: Any) -> str:
    try:
        from data_loader import SmartLoader

        for loader_cls in SmartLoader._loaders:
            if loader_cls().can_handle(upload):
                return loader_cls.__name__
        return "none"
    except Exception:
        return "unknown"
    finally:
        if hasattr(upload, "seek"):
            try:
                upload.seek(0)
            except Exception:
                pass


def describe_upload(upload: Any) -> Dict[str, Any]:
    """Describe the structure of an uploaded file without including its data."""
    name = str(getattr(upload, "name", upload))
    info: Dict[str, Any] = {
        "extension": name.rsplit(".", 1)[-1].lower() if "." in name else "",
        "size_kb": None,
        "loader": _detected_loader(upload),
        "columns": [],
    }
    size = getattr(upload, "size", None)
    if size is None and hasattr(upload, "getvalue"):
        size = len(upload.getvalue())
    if size is not None:
        info["size_kb"] = round(size / 1024)

    sample = _read_sample(upload)
    if sample is None:
        return info
    for col in list(sample.columns)[:MAX_COLUMNS]:
        values = sample[col].dropna().astype(str)
        numeric = pd.to_numeric(values, errors="coerce")
        non_numeric = values[numeric.isna()]
        patterns: List[str] = []
        for v in non_numeric:
            p = mask_value(v)
            if p not in patterns:
                patterns.append(p)
            if len(patterns) >= MAX_EXAMPLES:
                break
        info["columns"].append({
            "name": _mask_header(col),
            "non_numeric": f"{len(non_numeric)}/{len(values)}",
            "patterns": patterns,
        })
    return info


def safe_error_text(error: BaseException, upload: Any = None, max_len: int = 300) -> str:
    """Error message with the file name removed and all digits masked.

    Error messages can quote file names (which may contain an address) and
    cell values (meter readings, timestamps), so neither may leave the app.
    """
    from data_loader import UnexpectedValueError

    if isinstance(error, UnexpectedValueError):
        patterns = ", ".join(mask_value(v) for v in error.examples)
        return f"column '{_mask_header(error.column)}' has {error.count} unexpected value(s), patterns: {patterns}"
    text = str(error)
    name = getattr(upload, "name", None)
    if name:
        text = text.replace(str(name), "<file>")
    return re.sub(r"[0-9]", "9", text)[:max_len]


def build_alert(error: BaseException, info: Dict[str, Any], upload: Any = None) -> str:
    """Format the alert text (see module docstring for what is and isn't included)."""
    lines = [
        f"Upload failed: {type(error).__name__}: {safe_error_text(error, upload)}",
        f"File: .{info.get('extension')} ({info.get('size_kb')} KB), detected loader: {info.get('loader')}",
        "Columns (non-numeric/sampled, masked patterns):",
    ]
    for col in info.get("columns", []):
        patterns = ", ".join(col["patterns"]) if col["patterns"] else "-"
        lines.append(f"- {col['name']}: {col['non_numeric']}  {patterns}")
    return "\n".join(lines)


def send_alert(webhook_url: Optional[str], text: str) -> bool:
    """Post the alert. Returns True on success; never raises."""
    if not webhook_url:
        return False
    try:
        if "ntfy" in webhook_url:
            resp = requests.post(
                webhook_url,
                data=text.encode("utf-8"),
                headers={"Title": "BattWatt: upload failed", "Tags": "warning"},
                timeout=TIMEOUT_SECONDS,
            )
        elif "discord" in webhook_url:
            resp = requests.post(webhook_url, json={"content": text[:1900]}, timeout=TIMEOUT_SECONDS)
        else:
            resp = requests.post(webhook_url, json={"text": text}, timeout=TIMEOUT_SECONDS)
        return resp.ok
    except Exception:
        return False


def report_upload_error(error: BaseException, upload: Any, webhook_url: Optional[str]) -> bool:
    """Describe the failed upload and send the alert. Returns True if it was sent."""
    try:
        return send_alert(webhook_url, build_alert(error, describe_upload(upload), upload))
    except Exception:
        return False
