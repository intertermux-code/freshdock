import json
import socket
import time
import urllib.parse
from typing import Dict, Optional


class DockerAPIError(Exception):
    pass


class MockDockerClient:
    def __init__(self):
        self._containers = {}
        self._images = {}
        self._next_health = None  # health status for next created container
        self.created_with = []  # records of (image, kwargs) passed to create_container

    def set_container(self, container_id: str, image: str, health_status: str = "healthy", labels: Optional[dict] = None):
        self._containers[container_id] = {
            "Id": container_id,
            "Config": {"Image": image, "Labels": labels or {}},
            "Image": image,
            "State": {"Health": {"Status": health_status}} if health_status is not None else {"Health": None},
        }

    def set_image(self, image_ref: str, digest: str):
        self._images[image_ref] = digest

    def set_next_health(self, health_status: str = "healthy"):
        self._next_health = health_status

    def get_container(self, container_id: str):
        return self._containers.get(container_id)

    def inspect_container(self, container_id: str):
        c = self._containers.get(container_id)
        if not c:
            raise DockerAPIError(f"Container {container_id} not found")
        return c

    def list_containers(self):
        return list(self._containers.values())

    def pull(self, image: str):
        return {"status": "downloaded"}

    def stop_container(self, container_id: str):
        return True

    def remove_container(self, container_id: str, force: bool = True):
        return True

    def create_container(self, image: str, **kwargs):
        self.created_with.append((image, kwargs))
        cid = f"mock_{len(self._containers)}"
        health = self._next_health if self._next_health is not None else "starting"
        self._next_health = None
        self._containers[cid] = {
            "Id": cid,
            "Config": {"Image": image},
            "Image": image,
            "State": {"Health": {"Status": health}} if health else {"Health": None},
        }
        return {"Id": cid}

    def start_container(self, container_id: str):
        return True

    def wait_health(self, container_id: str, timeout: int = 30):
        c = self._containers.get(container_id)
        if not c:
            raise DockerAPIError("missing")
        health = c.get("State", {}).get("Health")
        if health and health.get("Status") == "healthy":
            return True
        if health and health.get("Status") == "unhealthy":
            return False
        # default to True if no explicit unhealthy
        if health and health.get("Status") == "starting":
            return False  # treat starting as timeout/unhealthy for our purposes
        return True


class DockerEngine:
    """Thin wrapper around the Docker Engine HTTP API (unix socket, stdlib only)."""

    def __init__(self, unix_socket: str = "/var/run/docker.sock", client=None):
        self.unix_socket = unix_socket
        self.client = client if client is not None else _UnixSocketClient(unix_socket)

    def inspect_container(self, container_id: str):
        return self._request("GET", f"/containers/{container_id}/json")

    def list_containers(self):
        return self._request("GET", "/containers/json")

    def pull(self, image: str):
        return self._request("POST", "/images/create", params={"fromImage": image})

    def stop_container(self, container_id: str, timeout: int = 10):
        return self._request("POST", f"/containers/{container_id}/stop", params={"t": str(timeout)})

    def remove_container(self, container_id: str, force: bool = True):
        return self._request("DELETE", f"/containers/{container_id}", params={"force": str(force).lower()})

    def create_container(self, image: str, name: Optional[str] = None, **kwargs):
        # The container name is a query parameter, everything else is the create body.
        body = {k: v for k, v in kwargs.items() if v is not None}
        body["Image"] = image
        params = {"name": name} if name else None
        return self._request("POST", "/containers/create", data=body, params=params)

    def start_container(self, container_id: str):
        return self._request("POST", f"/containers/{container_id}/start")

    def wait_health(self, container_id: str, timeout: int = 60, poll_interval: int = 2):
        """Block until the container reports healthy, unhealthy, or the timeout hits.

        Containers without a healthcheck are treated as healthy once running.
        """
        deadline = time.time() + timeout
        while True:
            try:
                info = self.inspect_container(container_id)
            except DockerAPIError:
                return False
            state = (info or {}).get("State") or {}
            if state.get("Running") is False:
                return False
            health = state.get("Health") or {}
            status = health.get("Status")
            if status == "healthy":
                return True
            if status == "unhealthy":
                return False
            if not health and state.get("Running"):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(poll_interval)

    def _request(self, method: str, path: str, data: Optional[dict] = None, params: Optional[dict] = None):
        if hasattr(self.client, "request"):
            return self.client.request(method, path, data, params)
        return {}


class _UnixSocketClient:
    """Minimal HTTP/1.0 client over an AF_UNIX socket (Docker Engine API)."""

    def __init__(self, unix_socket: str, timeout: int = 30):
        self.unix_socket = unix_socket
        self.timeout = timeout

    def request(self, method: str, path: str, data: Optional[dict] = None, params: Optional[dict] = None):
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        body = json.dumps(data).encode("utf-8") if data is not None else b""
        lines = [f"{method} {path}{query} HTTP/1.0", "Host: localhost", "Connection: close"]
        if body:
            lines += ["Content-Type: application/json", f"Content-Length: {len(body)}"]
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            try:
                sock.connect(self.unix_socket)
            except OSError as e:
                raise DockerAPIError(f"cannot reach docker socket {self.unix_socket}: {e}")
            sock.sendall(raw)
            resp = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                resp += chunk
        finally:
            sock.close()

        head, _, payload = resp.partition(b"\r\n\r\n")
        head_lines = head.split(b"\r\n")
        status_line = head_lines[0].decode("latin-1") if head_lines else ""
        try:
            status = int(status_line.split()[1])
        except (IndexError, ValueError):
            raise DockerAPIError(f"unparseable response from docker socket: {status_line!r}")
        headers = {}
        for line in head_lines[1:]:
            name, _, value = line.decode("latin-1").partition(":")
            headers[name.strip().lower()] = value.strip()
        if headers.get("transfer-encoding", "").lower() == "chunked":
            payload = _dechunk(payload)
        if status >= 400:
            detail = payload[:200].decode("latin-1", "replace")
            raise DockerAPIError(f"docker API {method} {path} -> HTTP {status}: {detail}")
        if not payload:
            return {}
        try:
            return json.loads(payload.decode("utf-8"))
        except ValueError:
            return payload.decode("utf-8", "replace")


def _dechunk(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while True:
        j = data.find(b"\r\n", i)
        if j == -1:
            break
        try:
            size = int(data[i:j].split(b";")[0].strip(), 16)
        except ValueError:
            break
        i = j + 2
        if size == 0:
            break
        out += data[i : i + size]
        i += size + 2
    return bytes(out)
