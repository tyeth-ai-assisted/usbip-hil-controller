"""M4.5 tests: GitDeploy adapter for SBC jobs."""

from pathlib import PurePosixPath
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from hil_controller.adapters.git_deploy import GitDeployAdapter
from hil_controller.hosts.base import ExecResult


def make_exec_result(exit_status=0, stdout="", stderr=""):
    r = MagicMock(spec=ExecResult)
    r.exit_status = exit_status
    r.stdout = stdout
    r.stderr = stderr
    return r


@pytest.fixture
def mock_transport():
    t = AsyncMock()
    t.exec = AsyncMock(return_value=make_exec_result(0))
    t.copy_to = AsyncMock(return_value=None)
    t.copy_from = AsyncMock(return_value=None)
    return t


@pytest.fixture
def git_deploy(mock_transport):
    return GitDeployAdapter(
        transport=mock_transport,
        job_id="job-abc",
        source={
            "repo": "https://github.com/adafruit/Wippersnapper_Python.git",
            "ref": "main",
            "submodules": False,
            "shallow": True,
            "setup": ["pip", "install", "-e", ".[test]"],
        },
        params={"entry": "python", "args": ["-m", "pytest", "-m", "eink_large", "-v"]},
        work_dir=PurePosixPath("/tmp/hil/job-abc"),
    )


@pytest.mark.asyncio
async def test_deploy_clones_repo(git_deploy, mock_transport):
    await git_deploy.deploy()
    calls = [str(c) for c in mock_transport.exec.call_args_list]
    assert any("git" in c and "clone" in c for c in calls)


@pytest.mark.asyncio
async def test_deploy_runs_setup_command(git_deploy, mock_transport):
    await git_deploy.deploy()
    calls = [str(c) for c in mock_transport.exec.call_args_list]
    assert any("pip" in c and "install" in c for c in calls)


@pytest.mark.asyncio
async def test_run_returns_pass_on_zero_exit(git_deploy, mock_transport):
    mock_transport.exec.return_value = make_exec_result(0, stdout="1 passed\n")
    result = await git_deploy.run()
    assert result == "pass"


@pytest.mark.asyncio
async def test_run_returns_fail_on_nonzero_exit(git_deploy, mock_transport):
    mock_transport.exec.return_value = make_exec_result(1, stdout="1 failed\n")
    result = await git_deploy.run()
    assert result == "fail"


@pytest.mark.asyncio
async def test_cleanup_removes_workdir(git_deploy, mock_transport):
    await git_deploy.cleanup()
    calls = [str(c) for c in mock_transport.exec.call_args_list]
    assert any("rm" in c for c in calls)
