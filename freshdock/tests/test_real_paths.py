import json
import os
import socket
import tempfile
import threading
import unittest

from freshdock.db import Database
from freshdock.engine import DockerAPIError, DockerEngine, _UnixSocketClient
from freshdock.registry import MockRegistryClient, _parse_image, pin_digest
from freshdock.updater import Updater


class TestPinDigest(unittest.TestCase):
    def test_strips_tag(self):
        self.assertEqual(pin_digest("nginx:1.25", "sha256:abc"), "nginx@sha256:abc")

    def test_bare_name(self):
        self.assertEqual(pin_digest("nginx", "sha256:abc"), "nginx@sha256:abc")

    def test_keeps_registry_port(self):
        self.assertEqual(
            pin_digest("registry.example.com:5000/foo:bar", "sha256:abc"),
            "registry.example.com:5000/foo@sha256:abc",
        )

    def test_replaces_old_digest(self):
        self.assertEqual(
            pin_digest("nginx@sha256:old", "sha256:new"),
            "nginx@sha256:new",
        )


class TestParseImage(unittest.TestCase):
    def test_docker_hub_short(self):
        p = _parse_image("nginx:latest")
        self.assertEqual(p["registry"], "registry-1.docker.io")
        self.assertEqual(p["repo"], "library/nginx")
        self.assertEqual(p["tag"], "latest")

    def test_custom_registry(self):
        p = _parse_image("registry.example.com:5000/team/app:1.2")
        self.assertEqual(p["registry"], "registry.example.com:5000")
        self.assertEqual(p["repo"], "team/app")
        self.assertEqual(p["tag"], "1.2")

    def test_docker_hub_namespace_keeps_its_own_path(self):
        p = _parse_image("myorg/app:1.0")
        self.assertEqual(p["registry"], "registry-1.docker.io")
        self.assertEqual(p["repo"], "myorg/app")
        self.assertEqual(p["tag"], "1.0")

    def test_docker_hub_namespace_two_levels(self):
        p = _parse_image("myorg/sub/app")
        self.assertEqual(p["repo"], "myorg/sub/app")
        self.assertEqual(p["tag"], "latest")


class TestWatchedEntries(unittest.TestCase):
    def _db(self):
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        return Database(handle.name), handle.name

    def test_watching_the_same_image_twice_keeps_one_entry(self):
        db, _ = self._db()
        self.addCleanup(db.close)
        db.watch(image="nginx:latest")
        db.watch(image="nginx:latest")
        self.assertEqual(len(db.list_watched()), 1)

    def test_digest_of_one_container_leaves_the_others_alone(self):
        db, _ = self._db()
        self.addCleanup(db.close)
        db.watch(container="c1", image="nginx:latest")
        db.watch(container="c2", image="nginx:latest")
        db.update_last_digest(container="c1", image="nginx:latest", digest="sha256:new")
        digests = {(e["container"], e["last_digest"]) for e in db.list_watched()}
        self.assertEqual(digests, {("c1", "sha256:new"), ("c2", None)})

    def test_digest_of_an_image_entry_leaves_container_entries_alone(self):
        db, _ = self._db()
        self.addCleanup(db.close)
        db.watch(image="nginx:latest")
        db.watch(container="c1", image="nginx:latest")
        db.update_last_digest(container=None, image="nginx:latest", digest="sha256:new")
        digests = {(e["container"], e["last_digest"]) for e in db.list_watched()}
        self.assertEqual(digests, {(None, "sha256:new"), ("c1", None)})


def _serve_once(sock_path, response, ready):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    srv.bind(sock_path)
    srv.listen(1)
    ready.set()  # signal only after the socket is actually listening
    conn, _ = srv.accept()
    try:
        conn.recv(65536)  # read the request; content asserted via response choice
        conn.sendall(response)
    finally:
        conn.close()
        srv.close()


class TestUnixSocketClient(unittest.TestCase):
    def _run(self, response):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "docker.sock")
        ready = threading.Event()
        t = threading.Thread(target=_serve_once, args=(path, response, ready), daemon=True)
        t.start()
        ready.wait(timeout=5)
        client = _UnixSocketClient(path, timeout=5)
        return client, t

    def test_get_json(self):
        body = json.dumps([{"Id": "abc"}]).encode()
        resp = b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(body) + body
        client, t = self._run(resp)
        self.assertEqual(client.request("GET", "/containers/json"), [{"Id": "abc"}])
        t.join(timeout=5)

    def test_post_with_body(self):
        body = json.dumps({"Id": "new"}).encode()
        resp = b"HTTP/1.0 201 Created\r\nContent-Length: %d\r\n\r\n" % len(body) + body
        client, t = self._run(resp)
        self.assertEqual(
            client.request("POST", "/containers/create", data={"Image": "nginx@sha256:x"}, params={"name": "web"}),
            {"Id": "new"},
        )
        t.join(timeout=5)

    def test_error_raises(self):
        resp = b"HTTP/1.0 404 Not Found\r\nContent-Length: 19\r\n\r\nno such container\r\n"
        client, t = self._run(resp)
        with self.assertRaises(DockerAPIError):
            client.request("GET", "/containers/nope/json")
        t.join(timeout=5)

    def test_missing_socket_raises(self):
        client = _UnixSocketClient("/nonexistent/docker.sock", timeout=2)
        with self.assertRaises(DockerAPIError):
            client.request("GET", "/version")


class TestRollbackPinsOldDigest(unittest.TestCase):
    def test_rollback_recreates_with_old_digest(self):
        from freshdock.engine import MockDockerClient

        with tempfile.NamedTemporaryFile(delete=False) as f:
            db_path = f.name
        try:
            db = Database(db_path)
            db.watch(container="c1", image="nginx:latest", last_digest="sha256:old")
            db.close()

            mock_engine = MockDockerClient()
            mock_engine.set_container("c1", "nginx:latest", health_status="healthy")
            mock_engine.set_next_health("unhealthy")
            mock_registry = MockRegistryClient()
            mock_registry.set_digest("nginx:latest", "sha256:new")

            db = Database(db_path)
            updater = Updater(db, mock_engine, registry=mock_registry)
            updater.run_once()
            db.close()

            created = [image for image, _ in mock_engine.created_with]
            self.assertIn("nginx@sha256:new", created)
            self.assertIn("nginx@sha256:old", created)
        finally:
            os.unlink(db_path)


if __name__ == "__main__":
    unittest.main()
