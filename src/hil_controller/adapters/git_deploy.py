"""GitDeploy adapter: clone → setup → materialise secrets → run on SBC via SSH transport."""

from __future__ import annotations

import json
import logging
import shlex
from pathlib import PurePosixPath
from typing import Any

log = logging.getLogger(__name__)

# Supported format tokens (may be combined with '+', e.g. "json+env")
_FORMATS = frozenset({"env", "json", "dotenv"})


class GitDeployAdapter:
    """Fulfils the DeviceAdapter protocol for SBC (git-source) jobs.

    The adapter is responsible for:
      1. acquire()  — no-op for SBC (no hardware reset needed)
      2. deploy()   — git clone + optional setup + secrets materialisation
      3. run()      — invoke entry point, return 'pass'/'fail'
      4. cleanup()  — rm -rf the work dir
      5. release()  — no-op

    Secrets:
      Pass ``secrets`` as a flat ``{key: value}`` dict.  ``secrets_format``
      controls how they reach the test process:

      * ``"env"``    — injected as subprocess env vars only (nothing on disk)
      * ``"json"``   — written as ``secrets.json`` in the work dir
      * ``"dotenv"`` — written as ``.env`` in the work dir
      * Combine with ``+``, e.g. ``"json+env"``

      Default is ``"env"``.  After the job is done the worker purges the
      values from the DB; the adapter never stores them beyond process memory.
    """

    def __init__(
        self,
        transport: Any,
        job_id: str,
        source: dict[str, Any],
        params: dict[str, Any],
        work_dir: PurePosixPath | None = None,
        secrets_dest: PurePosixPath | None = None,
        secrets: dict[str, str] | None = None,
        secrets_format: str = "env",
    ) -> None:
        self.transport = transport
        self.job_id = job_id
        self.source = source
        self.params = params
        self.work_dir = work_dir or PurePosixPath(f"/tmp/hil/{job_id}")
        self.secrets_dest = secrets_dest
        self._secrets: dict[str, str] = dict(secrets or {})
        self._secrets_format: str = secrets_format
        self._deploy_stdout: str = ""
        self._deploy_stderr: str = ""
        self._run_stdout: str = ""
        self._run_stderr: str = ""

    async def acquire(self) -> None:
        pass

    async def reset(self) -> None:
        pass

    async def flash(self, artifact: dict) -> None:
        await self.deploy()

    async def open_serial(self):
        return iter([])

    async def release(self) -> None:
        pass

    # ---------------------------------------------------------------------- #
    # SBC-specific operations                                                  #
    # ---------------------------------------------------------------------- #

    async def deploy(self) -> None:
        self._deploy_stdout = ""
        self._deploy_stderr = ""

        repo = self.source["repo"]
        pat = self.source.get("pat")
        if pat and repo.startswith("https://"):
            repo = repo.replace("https://", f"https://{pat}@", 1)
        ref = self.source.get("ref", "main")
        shallow = self.source.get("shallow", True)
        submodules = self.source.get("submodules", False)
        setup = self.source.get("setup") or []

        # mkdir -p workdir
        await self.transport.exec(["mkdir", "-p", str(self.work_dir)])

        # git clone
        clone_cmd = ["git", "clone"]
        if shallow:
            clone_cmd += ["--depth", "1"]
        if submodules:
            clone_cmd += ["--recurse-submodules"]
        clone_cmd += ["--branch", ref, repo, str(self.work_dir)]
        # echo command using original URL (PAT redacted)
        display_clone = ["git", "clone"]
        if shallow:
            display_clone += ["--depth", "1"]
        if submodules:
            display_clone += ["--recurse-submodules"]
        display_clone += ["--branch", ref, self.source["repo"], str(self.work_dir)]
        self._deploy_stdout += f"$ {shlex.join(display_clone)}\n"
        result = await self.transport.exec(clone_cmd)
        self._deploy_stdout += result.stdout
        self._deploy_stderr += result.stderr
        if result.exit_status != 0:
            raise RuntimeError(f"git clone failed (exit {result.exit_status}): {result.stderr}")

        # setup command (e.g. pip install)
        if setup:
            self._deploy_stdout += f"$ {shlex.join(setup)}\n"
            result = await self.transport.exec(setup, cwd=str(self.work_dir))
            self._deploy_stdout += result.stdout
            self._deploy_stderr += result.stderr
            if result.exit_status != 0:
                log.warning("setup command exited %d: %s", result.exit_status, result.stderr)

        # materialise secrets files
        if self._secrets:
            fmts = {t.strip() for t in self._secrets_format.split("+")}
            if "json" in fmts:
                await self._write_secrets_json()
            if "dotenv" in fmts:
                await self._write_secrets_dotenv()

    async def run(self) -> str:
        entry = self.params.get("entry", "python")
        args = self.params.get("args", [])
        argv = [entry] + list(args)

        env: dict[str, str] | None = None
        if self._secrets:
            fmts = {t.strip() for t in self._secrets_format.split("+")}
            if "env" in fmts:
                env = dict(self._secrets)

        extra_env = self.params.get("extra_env") or {}
        if extra_env:
            env = {**(env or {}), **extra_env}

        result = await self.transport.exec(argv, cwd=str(self.work_dir), env=env)
        self._run_stdout = result.stdout
        self._run_stderr = result.stderr
        log.info("test exit %d", result.exit_status)
        return "pass" if result.exit_status == 0 else "fail"

    async def cleanup(self) -> None:
        await self.transport.exec(["rm", "-rf", str(self.work_dir)])
        if self.secrets_dest:
            await self.transport.exec(["rm", "-f", str(self.secrets_dest)])

    # ---------------------------------------------------------------------- #
    # Secrets materialisation helpers                                          #
    # ---------------------------------------------------------------------- #

    async def _write_secrets_json(self) -> None:
        dest = str(self.work_dir / "secrets.json")
        payload = json.dumps(self._secrets, indent=2).encode()
        await self.transport.exec(["tee", dest], stdin=payload)
        log.debug("wrote secrets.json to %s", dest)

    async def _write_secrets_dotenv(self) -> None:
        dest = str(self.work_dir / ".env")
        lines = "\n".join(f"{k}={v}" for k, v in self._secrets.items()) + "\n"
        await self.transport.exec(["tee", dest], stdin=lines.encode())
        log.debug("wrote .env to %s", dest)
