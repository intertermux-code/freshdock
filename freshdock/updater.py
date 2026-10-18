import sys
import time
from typing import Optional

from .engine import DockerAPIError
from .registry import RateLimitedError, RegistryClient, RegistryError, pin_digest
from .notifier import Notifier


def _log(message: str):
    print(f"freshdock: {message}", file=sys.stderr)


class Updater:
    """Poll registries for watched containers/images and apply health-gated updates.

    Update flow per container:
      1. Inspect the running container to capture its runtime config.
      2. Pull the new image pinned to its digest.
      3. Recreate the container with the same config + name, start it.
      4. Wait for the healthcheck; on unhealthy/timeout, remove the failed
         container and recreate it pinned to the previous digest (rollback).
    """

    def __init__(self, db, engine, interval: int = 300, health_timeout: int = 60,
                 registry: Optional[RegistryClient] = None, notifier: Optional[Notifier] = None):
        self.db = db
        self.engine = engine
        self.interval = interval
        self.health_timeout = health_timeout
        self.registry = registry if registry is not None else RegistryClient(db)
        self.notifier = notifier
        self.running = False

    def run_once(self):
        self._check_all()

    def run_forever(self):
        self.running = True
        while self.running:
            self._check_all()
            time.sleep(self.interval)

    def stop(self):
        self.running = False

    def _check_all(self):
        for entry in self.db.list_watched():
            try:
                self._check_entry(entry)
            except Exception as e:
                _log(f"check failed for {entry.get('container') or entry.get('image')}: {e}")

    def _check_entry(self, entry: dict):
        container = entry.get("container")
        image = entry.get("image")
        last_digest = entry.get("last_digest")

        current_image = image
        if container:
            try:
                info = self.engine.inspect_container(container)
                current_image = info.get("Config", {}).get("Image") or info.get("Image")
            except DockerAPIError:
                current_image = image  # container gone; fall back to the configured image

        if not current_image:
            return

        try:
            cached = self.db.get_manifest_cache(current_image) if self.db else None
            # Registry clients return (info, digest, etag); tolerate shorter tuples.
            result = self.registry.get_manifest(current_image)
            if len(result) == 3:
                _, new_digest, etag = result
            else:
                new_digest = result[0]
                etag = result[1] if len(result) > 1 else None
            if cached and new_digest == cached.get("digest") and etag == cached.get("etag"):
                return  # unchanged manifest; no registry round-trip burned
            if self.db:
                self.db.cache_manifest(current_image, new_digest, etag)
        except RateLimitedError as e:
            retry_after = e.headers.get("Retry-After") or e.headers.get("retry-after")
            try:
                wait = max(int(retry_after), 1)
            except (TypeError, ValueError):
                wait = 60
            _log(f"registry rate-limited; backing off {wait}s")
            time.sleep(wait)
            return
        except RegistryError as e:
            _log(f"registry check failed for {current_image}: {e}")
            return

        if not new_digest:
            return
        if last_digest and new_digest == last_digest:
            return

        if container and current_image:
            self._perform_update(container, current_image, last_digest, new_digest)
        else:
            if self.db:
                self.db.update_last_digest(container=container, image=image, digest=new_digest)
                self.db.record_history(
                    container=container, image=image,
                    old_digest=last_digest, new_digest=new_digest, status="detected",
                )

    def _perform_update(self, container: str, image: str, old_digest: Optional[str], new_digest: str):
        try:
            info = self.engine.inspect_container(container)
        except DockerAPIError as e:
            _log(f"cannot inspect {container}: {e}; skipping update")
            return
        config = info.get("Config") or {}
        host_config = info.get("HostConfig") or {}
        name = (info.get("Name") or "").lstrip("/") or None

        create_kwargs = self._create_kwargs(config, host_config)
        new_ref = pin_digest(image, new_digest)
        try:
            self.engine.pull(new_ref)
        except DockerAPIError as e:
            _log(f"pull of {new_ref} failed: {e}; skipping update")
            return

        try:
            self.engine.stop_container(container)
        except DockerAPIError:
            pass
        try:
            self.engine.remove_container(container, force=True)
        except DockerAPIError:
            pass

        new_cid = self._recreate(new_ref, name, create_kwargs)
        if new_cid is None:
            _log(f"failed to recreate {container} with {new_ref}")
            return
        healthy = self._start_and_wait_health(new_cid)

        if healthy:
            if self.db:
                self.db.update_last_digest(container=container, image=image, digest=new_digest)
                self.db.record_history(
                    container=container, image=image,
                    old_digest=old_digest, new_digest=new_digest, status="updated",
                )
            self._notify("freshdock: updated", f"{container} updated to {new_digest[:19]}")
            return

        # Rollback: recreate pinned to the previous known-good digest.
        _log(f"{container} unhealthy after update; rolling back")
        self._remove_quietly(new_cid)
        old_ref = pin_digest(image, old_digest) if old_digest else image
        self._recreate(old_ref, name, create_kwargs)
        if self.db:
            self.db.record_history(
                container=container, image=image,
                old_digest=old_digest, new_digest=new_digest,
                status="rollback", details={"reason": "unhealthy"},
            )
        self._notify("freshdock: rollback", f"{container} rolled back after unhealthy update")

    def _create_kwargs(self, config: dict, host_config: dict) -> dict:
        """Carry the container's runtime config over to the recreated container."""
        return {
            "Env": config.get("Env"),
            "Cmd": config.get("Cmd"),
            "Entrypoint": config.get("Entrypoint"),
            "WorkingDir": config.get("WorkingDir"),
            "User": config.get("User"),
            "ExposedPorts": config.get("ExposedPorts"),
            "Volumes": config.get("Volumes"),
            "HostConfig": {
                "Binds": host_config.get("Binds"),
                "PortBindings": host_config.get("PortBindings"),
                "RestartPolicy": host_config.get("RestartPolicy"),
                "NetworkMode": host_config.get("NetworkMode"),
                "Links": host_config.get("Links"),
                "ExtraHosts": host_config.get("ExtraHosts"),
                "Devices": host_config.get("Devices"),
                "Privileged": host_config.get("Privileged"),
            },
        }

    def _recreate(self, image_ref: str, name: Optional[str], create_kwargs: dict) -> Optional[str]:
        try:
            created = self.engine.create_container(image_ref, name=name, **create_kwargs)
        except DockerAPIError as e:
            _log(f"create of {image_ref} failed: {e}")
            return None
        cid = created.get("Id") if isinstance(created, dict) else str(created)
        try:
            self.engine.start_container(cid)
        except DockerAPIError as e:
            _log(f"start of {cid} failed: {e}")
            return None
        return cid

    def _start_and_wait_health(self, cid: str) -> bool:
        try:
            return bool(self.engine.wait_health(cid, timeout=self.health_timeout))
        except Exception as e:
            _log(f"health wait failed for {cid}: {e}")
            return False

    def _remove_quietly(self, cid: str):
        for op in (self.engine.stop_container, self.engine.remove_container):
            try:
                if op is self.engine.remove_container:
                    op(cid, force=True)
                else:
                    op(cid)
            except DockerAPIError:
                pass

    def _notify(self, title: str, message: str):
        if not self.notifier:
            return
        try:
            self.notifier.send(title, message)
        except Exception as e:
            _log(f"notification failed: {e}")
