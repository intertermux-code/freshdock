import json
import sys
import urllib.parse
import urllib.request
from typing import Optional


def _log(message: str):
    print(f"freshdock: {message}", file=sys.stderr)


class Notifier:
    def send(self, title: str, message: str, priority: Optional[str] = None):
        raise NotImplementedError


class NtfyNotifier(Notifier):
    def __init__(self, topic: str, base_url: str = "https://ntfy.sh"):
        self.topic = topic
        self.base_url = base_url

    def send(self, title: str, message: str, priority: Optional[str] = None):
        url = f"{self.base_url}/{urllib.parse.quote(self.topic)}"
        data = message.encode("utf-8")
        headers = {"Title": title.encode("utf-8") if hasattr(title, "encode") else title}
        if priority:
            headers["Priority"] = str(priority)
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read()
        except Exception as e:
            _log(f"ntfy notification failed: {e}")
            return None


class WebhookNotifier(Notifier):
    def __init__(self, url: str, timeout: int = 10):
        self.url = url
        self.timeout = timeout

    def send(self, title: str, message: str, priority: Optional[str] = None, fields: Optional[dict] = None):
        payload = {"title": title, "body": message, "fields": fields or {}}
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except Exception as e:
            _log(f"webhook notification failed: {e}")
            return None


class DiscordNotifier(Notifier):
    def __init__(self, url: str, timeout: int = 10):
        self.url = url
        self.timeout = timeout

    def send(self, title: str, message: str, priority: Optional[str] = None, fields: Optional[dict] = None):
        embed = {
            "title": title,
            "description": message,
            "fields": [{"name": str(k), "value": str(v)} for k, v in (fields or {}).items()],
        }
        payload = {"embeds": [embed]}
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except Exception as e:
            _log(f"discord notification failed: {e}")
            return None


class MockNotifier(Notifier):
    def __init__(self):
        self.sent = []

    def send(self, title: str, message: str, priority: Optional[str] = None):
        self.sent.append({"title": title, "message": message, "priority": priority})
        return True
