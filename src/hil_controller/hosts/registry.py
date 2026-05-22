"""Host registry: loads topology YAML, provides adapters to the scheduler."""

from __future__ import annotations

import json
import logging
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

log = logging.getLogger(__name__)


class HostRegistry:
    def __init__(self, topology_file: str) -> None:
        self.topology_file = topology_file
        self._hosts: list[dict[str, Any]] = []
        self._devices: list[dict[str, Any]] = []
        self._semaphores: dict[str, Any] = {}

    def load(self) -> None:
        if not self.topology_file:
            return
        path = Path(self.topology_file)
        if not path.exists():
            log.warning("Topology file not found: %s", path)
            return
        data = yaml.safe_load(path.read_text())
        self._hosts = data.get("hosts", [])
        self._devices = data.get("devices", [])
        import asyncio

        for h in self._hosts:
            max_jobs = h.get("max_concurrent_jobs", 1)
            if max_jobs is not None:
                self._semaphores[h["id"]] = asyncio.Semaphore(max_jobs)
            else:
                self._semaphores[h["id"]] = None  # unbounded

        log.info("Loaded %d hosts, %d devices from %s", len(self._hosts), len(self._devices), path)

    def find_device_for_job(self, request: dict[str, Any]) -> tuple[dict, dict] | None:
        """Return (host, device) for the given job request, or None if no seat."""
        target = request.get("target", {})
        device_sel = target.get("device", {})
        pool = target.get("pool", "public")

        for device in self._devices:
            if device.get("status", "available") != "available":
                continue
            if pool and device.get("pool") != pool:
                continue
            if device_sel.get("kind") and device["kind"] != device_sel["kind"]:
                continue
            if device_sel.get("model") and device["model"] != device_sel["model"]:
                continue
            if device_sel.get("id") and device["id"] != device_sel["id"]:
                continue
            # Find owning host
            host = next((h for h in self._hosts if h["id"] == device["host_id"]), None)
            if host is None:
                continue
            return host, device

        return None

    async def get_adapter(self, job_id: str) -> Any:
        from hil_controller.db.connection import get_db, update_job_state

        # We need the job's request to resolve a device
        # Import app state via the global scheduler (passed at construction)
        # For now, return a no-op adapter; the scheduler wires up the db_path
        # and this method is overridden by _RegistryAdapter below
        from hil_controller.queue.scheduler import _FakeAdapter

        return _FakeAdapter()


class RealHostRegistry(HostRegistry):
    """Registry that returns real SSH-backed adapters."""

    def __init__(self, topology_file: str, db_path: str) -> None:
        super().__init__(topology_file)
        self.db_path = db_path

    async def get_adapter(self, job_id: str) -> Any:
        import json

        from hil_controller.adapters.git_deploy import GitDeployAdapter
        from hil_controller.db.connection import get_db, get_job, update_job_state
        from hil_controller.hosts.ssh import SSHTransport

        async with get_db(self.db_path) as db:
            row = await get_job(db, job_id)
        if row is None:
            from hil_controller.queue.scheduler import _FakeAdapter

            return _FakeAdapter()

        request = json.loads(row["request_json"])
        result = self.find_device_for_job(request)
        if result is None:
            log.warning("No matching device for job %s — using fake adapter", job_id)
            from hil_controller.queue.scheduler import _FakeAdapter

            return _FakeAdapter()

        host, device = result

        if host.get("kind") == "local":
            from hil_controller.hosts.local import LocalTransport

            transport = LocalTransport()
        else:
            transport = SSHTransport(
                host=host["addr"],
                user=host.get("ssh_user", "pi"),
                key_path=Path(host["ssh_key_path"]) if host.get("ssh_key_path") else None,
                known_hosts=host.get("known_hosts"),
            )

        # Record assignment in the DB
        async with get_db(self.db_path) as db:
            await update_job_state(
                db,
                job_id,
                "assigned",
                assigned_host=host["id"],
                assigned_device=device["id"],
            )

        payload = request.get("payload") or {}
        params = request.get("params") or {}
        source = payload.get("source", {})
        secrets = request.get("secrets", {})
        secrets_format = params.get("secrets_format", "env")

        if not source:
            from hil_controller.adapters.shell_script import ShellScriptAdapter

            return ShellScriptAdapter(
                transport=transport,
                script=request.get("script", "true"),
            )

        return GitDeployAdapter(
            transport=transport,
            job_id=job_id,
            source=source,
            params=params,
            secrets=secrets,
            secrets_format=secrets_format,
        )
