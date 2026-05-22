"""GitDeploy adapter: clone → setup → run on SBC via SSH transport."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Any

log = logging.getLogger(__name__)


class GitDeployAdapter:
    """Fulfils the DeviceAdapter protocol for SBC (git-source) jobs.

    The adapter is responsible for:
      1. acquire()  — no-op for SBC (no hardware reset needed)
      2. deploy()   — git clone + optional setup command
      3. run()      — invoke entry point, return 'pass'/'fail'
      4. cleanup()  — rm -rf the work dir and secrets file
      5. release()  — no-op
    """

    def __init__(
        self,
        transport: Any,
        job_id: str,
        source: dict[str, Any],
        params: dict[str, Any],
        work_dir: PurePosixPath | None = None,
        secrets_dest: PurePosixPath | None = None,
    ) -> None:
        self.transport = transport
        self.job_id = job_id
        self.source = source
        self.params = params
        self.work_dir = work_dir or PurePosixPath(f"/tmp/hil/{job_id}")
        self.secrets_dest = secrets_dest
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
        result = await self.transport.exec(clone_cmd)
        self._deploy_stderr += result.stderr
        if result.exit_status != 0:
            raise RuntimeError(f"git clone failed (exit {result.exit_status}): {result.stderr}")

        # setup command (e.g. pip install)
        if setup:
            result = await self.transport.exec(setup, cwd=str(self.work_dir))
            self._deploy_stdout += result.stdout
            self._deploy_stderr += result.stderr
            if result.exit_status != 0:
                log.warning("setup command exited %d: %s", result.exit_status, result.stderr)

    async def run(self) -> str:
        entry = self.params.get("entry", "python")
        args = self.params.get("args", [])
        argv = [entry] + list(args)

        result = await self.transport.exec(argv, cwd=str(self.work_dir))
        self._run_stdout = result.stdout
        self._run_stderr = result.stderr
        log.info("test exit %d", result.exit_status)
        return "pass" if result.exit_status == 0 else "fail"

    async def cleanup(self) -> None:
        await self.transport.exec(["rm", "-rf", str(self.work_dir)])
        if self.secrets_dest:
            await self.transport.exec(["rm", "-f", str(self.secrets_dest)])
