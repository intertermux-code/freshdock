import urllib.parse
import urllib.request
from typing import Optional


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
        except Exception:
            # Swallow errors as per stdlib only - don't crash
            return None


class MockNotifier(Notifier):
    def __init__(self):
        self.sent = []

    def send(self, title: str, message: str, priority: Optional[str] = None):
        self.sent.append({"title": title, "message": message, "priority": priority})
        return True
