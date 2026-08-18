import hashlib
import hmac
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import requests

from quota_monitor.release_sink import (
    DeliveryResult,
    build_release_signal,
    canonical_body,
    deliver_outbox,
    send_release_signal,
)

SECRET = "release-signal-test-secret-contains-more-than-32-bytes"


class ReleaseSinkTests(unittest.TestCase):
    def setUp(self):
        self.signal = build_release_signal(
            [
                (("08/20/2026", "RHK", "R"), "quota-n", "quota-g"),
                (("08/20/2026", "RHK", "R"), "quota-n", "quota-g"),
            ],
            observed_at=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
        )

    def test_builds_canonical_deduplicated_contract(self):
        self.assertEqual(self.signal["schema"], "hkid.quota.release.v1")
        self.assertEqual(len(self.signal["event_id"]), 64)
        self.assertEqual(len(self.signal["released"]), 1)
        self.assertEqual(self.signal["released"][0]["date"], "2026-08-20")
        self.assertEqual(self.signal["observed_at"], "2026-08-18T08:00:00Z")

    @patch("quota_monitor.release_sink.time.time", return_value=1770000000)
    @patch("quota_monitor.release_sink.requests.post")
    def test_signs_exact_body_and_does_not_follow_redirects(self, post, _clock):
        post.return_value = Mock(status_code=202)

        result = send_release_signal(
            self.signal, "http://127.0.0.1:8765/internal/release", SECRET
        )

        self.assertIs(result, DeliveryResult.DELIVERED)
        kwargs = post.call_args.kwargs
        body = canonical_body(self.signal)
        expected = hmac.new(
            SECRET.encode(), b"1770000000." + body, hashlib.sha256
        ).hexdigest()
        self.assertEqual(kwargs["data"], body)
        self.assertEqual(kwargs["headers"]["X-HKID-Signature"], "v1=" + expected)
        self.assertFalse(kwargs["allow_redirects"])

    @patch("quota_monitor.release_sink.requests.post")
    def test_classifies_timeout_server_error_and_client_rejection(self, post):
        post.side_effect = requests.Timeout()
        self.assertIs(
            send_release_signal(self.signal, "https://example.test/hook", SECRET),
            DeliveryResult.RETRYABLE_FAILURE,
        )
        post.side_effect = None
        post.return_value = Mock(status_code=503)
        self.assertIs(
            send_release_signal(self.signal, "https://example.test/hook", SECRET),
            DeliveryResult.RETRYABLE_FAILURE,
        )
        post.return_value = Mock(status_code=401)
        self.assertIs(
            send_release_signal(self.signal, "https://example.test/hook", SECRET),
            DeliveryResult.PERMANENT_REJECTION,
        )

    def test_refuses_plaintext_non_loopback_and_short_secret(self):
        with self.assertRaises(ValueError):
            send_release_signal(self.signal, "http://example.test/hook", SECRET)
        with self.assertRaises(ValueError):
            send_release_signal(self.signal, "https://example.test/hook", "short")

    @patch("quota_monitor.release_sink.send_release_signal")
    def test_outbox_retains_only_retryable_failures(self, send):
        retry_signal = json.loads(json.dumps(self.signal))
        retry_signal["event_id"] = "a" * 64
        send.side_effect = [
            DeliveryResult.RETRYABLE_FAILURE,
            DeliveryResult.RETRYABLE_FAILURE,
            DeliveryResult.RETRYABLE_FAILURE,
            DeliveryResult.PERMANENT_REJECTION,
        ]

        remaining, rejected = deliver_outbox(
            [retry_signal, self.signal],
            url="https://example.test/hook",
            secret=SECRET,
            max_attempts=3,
            sleep=lambda _seconds: None,
        )

        self.assertEqual(remaining, [retry_signal])
        self.assertEqual(rejected, [self.signal["event_id"]])


if __name__ == "__main__":
    unittest.main()
