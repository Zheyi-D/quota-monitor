"""Signed, PII-free ReleaseSignal delivery to hkid-appointment-monitor."""

import hashlib
import hmac
import ipaddress
import json
import time
from datetime import date as calendar_date
from datetime import datetime, timezone
from enum import Enum
from urllib.parse import urlsplit

import requests

SCHEMA = "hkid.quota.release.v1"
KNOWN_OFFICES = {"FTO", "RHK", "RKO", "RTK", "TMO", "YLO"}
AVAILABLE_STATUSES = {"quota-g", "quota-y"}


class DeliveryResult(Enum):
    DELIVERED = "DELIVERED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_REJECTION = "PERMANENT_REJECTION"


def canonical_body(signal):
    """Serialize the exact bytes covered by the signature."""

    return json.dumps(signal, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _normalise_date(value):
    if not isinstance(value, str):
        raise TypeError("release signal date must be a string")
    try:
        if "-" in value:
            year, month, day = (int(part) for part in value.split("-"))
        else:
            month, day, year = (int(part) for part in value.split("/"))
        return calendar_date(year, month, day).isoformat()
    except (TypeError, ValueError):
        raise ValueError("release signal contains an invalid date") from None


def build_release_signal(newly_available, observed_at=None):
    """Convert core.detect_changes output into the strict v1 contract."""

    released = []
    seen = set()
    for (date_text, office_id, quota_type), _old_status, new_status in newly_available:
        if office_id not in KNOWN_OFFICES:
            raise ValueError("release signal contains an unknown office")
        if quota_type not in {"R", "K"}:
            raise ValueError("release signal contains an unknown quota type")
        if new_status not in AVAILABLE_STATUSES:
            raise ValueError("release signal contains a non-available status")
        iso_date = _normalise_date(date_text)
        key = (iso_date, office_id, quota_type)
        if key in seen:
            continue
        seen.add(key)
        released.append(
            {
                "date": iso_date,
                "office_id": office_id,
                "quota_type": quota_type,
                "status": new_status,
            }
        )
    if not released:
        raise ValueError("release signal requires at least one released row")
    released.sort(key=lambda row: (row["date"], row["office_id"], row["quota_type"]))
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    event_basis = {
        "observed_at": observed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "released": released,
    }
    event_id = hashlib.sha256(canonical_body(event_basis)).hexdigest()
    return {
        "schema": SCHEMA,
        "event_id": event_id,
        **event_basis,
    }


def _url_is_allowed(url):
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname:
        return True
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    if parsed.hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def send_release_signal(signal, url, secret, timeout_seconds=10):
    """Deliver once; never follow redirects or expose response bodies."""

    if not _url_is_allowed(url):
        raise ValueError("release webhook must use HTTPS or loopback HTTP")
    secret_bytes = secret.encode("utf-8")
    if len(secret_bytes) < 32:
        raise ValueError("release webhook secret must contain at least 32 bytes")
    body = canonical_body(signal)
    timestamp = str(int(time.time()))
    digest = hmac.new(
        secret_bytes, timestamp.encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()
    try:
        response = requests.post(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-HKID-Timestamp": timestamp,
                "X-HKID-Signature": "v1=" + digest,
            },
            timeout=timeout_seconds,
            allow_redirects=False,
        )
    except (requests.Timeout, requests.ConnectionError):
        return DeliveryResult.RETRYABLE_FAILURE
    if 200 <= response.status_code < 300:
        return DeliveryResult.DELIVERED
    if 500 <= response.status_code < 600:
        return DeliveryResult.RETRYABLE_FAILURE
    return DeliveryResult.PERMANENT_REJECTION


def deliver_outbox(
    pending,
    *,
    url,
    secret,
    timeout_seconds=10,
    max_attempts=3,
    backoff_seconds=1,
    sleep=time.sleep,
):
    """Retry transient failures and return (remaining, permanently_rejected_ids)."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    remaining = []
    rejected = []
    for signal in pending:
        result = DeliveryResult.RETRYABLE_FAILURE
        for attempt in range(max_attempts):
            result = send_release_signal(signal, url, secret, timeout_seconds)
            if result is not DeliveryResult.RETRYABLE_FAILURE:
                break
            if attempt + 1 < max_attempts:
                sleep(backoff_seconds * (2**attempt))
        if result is DeliveryResult.RETRYABLE_FAILURE:
            remaining.append(signal)
        elif result is DeliveryResult.PERMANENT_REJECTION:
            rejected.append(signal.get("event_id", "invalid"))
    return remaining, rejected
