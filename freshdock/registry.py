import json
import re
import urllib.parse
import urllib.request
from typing import Dict, Optional, Tuple


class RegistryError(Exception):
    pass


class RateLimitedError(Exception):
    def __init__(self, headers: Optional[Dict] = None):
        self.headers = headers or {}
        super().__init__("Rate limited")


def pin_digest(image_ref: str, digest: str) -> str:
    """Return the image reference pinned to an exact digest, e.g.
    ``nginx:1.25`` + ``sha256:abc`` -> ``nginx@sha256:abc``.
    """
    base = image_ref
    if "@" in base:
        base = base.split("@", 1)[0]
    else:
        # Strip :tag but not a :port in the registry host.
        slash = base.rfind("/")
        colon = base.rfind(":")
        if colon > slash:
            base = base[:colon]
    return f"{base}@{digest}"


class RegistryClient:
    """Manifest/digest checks against OCI registries (stdlib only).

    Polls with HEAD requests, caches bearer tokens per (registry, scope), and
    raises RateLimitedError on 429/503 so the caller can back off instead of
    burning the registry rate limit.
    """

    _ACCEPT = "application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json"

    def __init__(self, db=None):
        self.db = db
        self.token_cache = {}  # (registry, scope) -> bearer token

    def get_manifest(self, image: str, accept: Optional[str] = None) -> Tuple[Dict, Optional[str], Optional[str]]:
        parsed = _parse_image(image)
        repo = parsed["repo"]
        tag = parsed.get("tag") or "latest"
        registry = parsed.get("registry") or "registry-1.docker.io"
        scope = f"repository:{repo}:pull"

        def headers_with(auth):
            headers = {"Accept": accept or self._ACCEPT}
            if auth:
                headers["Authorization"] = auth
            return headers

        url = f"https://{registry}/v2/{repo}/manifests/{tag}"
        auth = self._cached_auth(registry, scope)
        try:
            return self._fetch(url, headers_with(auth))
        except _Unauthorized as exc:
            token = self._fetch_token(exc.challenge)
            if not token:
                raise RegistryError(f"registry auth failed for {registry}")
            self.token_cache[(registry, scope)] = token
            return self._fetch(url, headers_with(f"Bearer {token}"))

    def _cached_auth(self, registry: str, scope: str) -> Optional[str]:
        token = self.token_cache.get((registry, scope))
        return f"Bearer {token}" if token else None

    def _parse_image(self, image: str) -> Dict:
        return _parse_image(image)

    def _fetch(self, url: str, headers: Dict):
        for method in ("HEAD", "GET"):
            req = urllib.request.Request(url, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    digest = resp.headers.get("Docker-Content-Digest") or resp.headers.get("Etag")
                    etag = resp.headers.get("Etag")
                    return {"status": resp.status}, digest, etag
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise _Unauthorized(e.headers.get("WWW-Authenticate") or "")
                if e.code in (429, 503):
                    raise RateLimitedError(dict(e.headers))
                if e.code in (405, 501) and method == "HEAD":
                    continue  # registry refuses HEAD; retry with GET
                raise RegistryError(f"HTTP {e.code} for {url}")
        raise RegistryError(f"registry refused manifest request for {url}")

    def _fetch_token(self, challenge: str) -> Optional[str]:
        # e.g. Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:library/nginx:pull"
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = params.get("realm")
        if not realm:
            return None
        query = {k: params[k] for k in ("service", "scope") if k in params}
        url = realm + ("&" if "?" in realm else "?") + urllib.parse.urlencode(query)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("token") or data.get("access_token")
        except Exception:
            return None


class _Unauthorized(Exception):
    def __init__(self, challenge: str):
        self.challenge = challenge
        super().__init__("unauthorized")


def _parse_image(image: str) -> Dict:
    # Split off any @digest first.
    ref = image.split("@", 1)[0]
    if "/" in ref:
        first, rest = ref.split("/", 1)
        if "." in first or ":" in first or first == "localhost":
            registry, repo = first, rest
        else:
            registry, repo = "registry-1.docker.io", ("library/" + ref) if ref.count("/") == 1 and "." not in first else ref
    else:
        registry, repo = "registry-1.docker.io", f"library/{ref}"
    if ":" in repo:
        repo, tag = repo.rsplit(":", 1)
    else:
        tag = "latest"
    return {"registry": registry, "repo": repo, "tag": tag}


class MockRegistryClient:
    def __init__(self):
        self._digests = {}  # key -> digest
        self._rate_limited = False
        self._calls = 0

    def set_digest(self, image_key: str, digest: str):
        self._digests[image_key] = digest

    def set_rate_limited(self, limited: bool):
        self._rate_limited = limited

    def get_manifest(self, image: str, accept: Optional[str] = None):
        self._calls += 1
        if self._rate_limited:
            raise RateLimitedError({"Retry-After": "1"})
        key = image
        digest = self._digests.get(key) or "sha256:mockdigest123"
        return {"status": 200}, digest, f'"{digest}"'
