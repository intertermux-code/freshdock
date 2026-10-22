import json
import unittest
from unittest.mock import patch

from freshdock.cli import _default_notifier
from freshdock.notifier import DiscordNotifier, NtfyNotifier, WebhookNotifier


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"{}"


class _UrlCapture:
    def __init__(self):
        self.requests = []
        self.attempts = 0
        self.side_effect = None

    def __call__(self, req, timeout=None):
        self.attempts += 1
        if self.side_effect is not None:
            raise self.side_effect
        self.requests.append(
            {
                "url": req.full_url,
                "method": req.method,
                "headers": dict(req.header_items()),
                "body": json.loads(req.data.decode("utf-8")) if req.data else None,
            }
        )
        return _FakeResponse()


class TestWebhookNotifier(unittest.TestCase):
    def setUp(self):
        self.capture = _UrlCapture()
        self.patcher = patch("urllib.request.urlopen", self.capture)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_post_json_payload(self):
        notifier = WebhookNotifier("https://example.com/hooks/freshdock")
        result = notifier.send("title", "body", fields={"digest": "sha256:abc", "count": 3})

        self.assertEqual(result, b"{}")
        self.assertEqual(len(self.capture.requests), 1)
        req = self.capture.requests[0]
        self.assertEqual(req["url"], "https://example.com/hooks/freshdock")
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["headers"], {"Content-type": "application/json"})
        self.assertEqual(
            req["body"], {"title": "title", "body": "body", "fields": {"digest": "sha256:abc", "count": 3}}
        )

    def test_default_fields_empty(self):
        notifier = WebhookNotifier("https://example.com/hooks/freshdock")
        notifier.send("title", "body")
        self.assertEqual(self.capture.requests[0]["body"], {"title": "title", "body": "body", "fields": {}})

    def test_failure_logged_without_raising(self):
        self.capture.side_effect = OSError("down")
        try:
            notifier = WebhookNotifier("https://example.com/hooks/freshdock")
            self.assertIsNone(notifier.send("title", "body"))
            self.assertEqual(self.capture.attempts, 1)  # one attempt, no retry
        finally:
            self.capture.side_effect = None


class TestDefaultNotifier(unittest.TestCase):
    def test_ntfy_takes_precedence(self):
        notifiers = {
            "ntfy": NtfyNotifier("topic"),
            "webhook": WebhookNotifier("https://example.com/hooks/freshdock"),
        }
        self.assertIs(_default_notifier(notifiers), notifiers["ntfy"])

    def test_webhook_only_resolves_to_webhook(self):
        notifiers = {"webhook": WebhookNotifier("https://example.com/hooks/freshdock")}
        self.assertIs(_default_notifier(notifiers), notifiers["webhook"])

    def test_discord_only_resolves_to_discord(self):
        notifiers = {"discord": DiscordNotifier("https://discord.com/api/webhooks/123/abc")}
        self.assertIs(_default_notifier(notifiers), notifiers["discord"])

    def test_empty_resolves_to_none(self):
        self.assertIsNone(_default_notifier({}))

    def test_multiple_without_ntfy_resolves_to_none_and_logs(self):
        notifiers = {
            "webhook": WebhookNotifier("https://example.com/hooks/freshdock"),
            "discord": DiscordNotifier("https://discord.com/api/webhooks/123/abc"),
        }
        self.assertIsNone(_default_notifier(notifiers))


class TestDiscordNotifier(unittest.TestCase):
    def setUp(self):
        self.capture = _UrlCapture()
        self.patcher = patch("urllib.request.urlopen", self.capture)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_post_rich_embed_payload(self):
        notifier = DiscordNotifier("https://discord.com/api/webhooks/123/abc")
        notifier.send("freshdock: updated", "web updated to sha256:abcd...", fields={"image": "nginx:latest"})

        self.assertEqual(len(self.capture.requests), 1)
        req = self.capture.requests[0]
        self.assertEqual(req["url"], "https://discord.com/api/webhooks/123/abc")
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["headers"], {"Content-type": "application/json"})
        self.assertEqual(
            req["body"],
            {
                "embeds": [
                    {
                        "title": "freshdock: updated",
                        "description": "web updated to sha256:abcd...",
                        "fields": [{"name": "image", "value": "nginx:latest"}],
                    }
                ]
            },
        )

    def test_default_fields_empty(self):
        notifier = DiscordNotifier("https://discord.com/api/webhooks/123/abc")
        notifier.send("title", "body")
        self.assertEqual(
            self.capture.requests[0]["body"],
            {"embeds": [{"title": "title", "description": "body", "fields": []}]},
        )

    def test_failure_logged_without_raising(self):
        self.capture.side_effect = OSError("down")
        try:
            notifier = DiscordNotifier("https://discord.com/api/webhooks/123/abc")
            self.assertIsNone(notifier.send("title", "body"))
        finally:
            self.capture.side_effect = None
