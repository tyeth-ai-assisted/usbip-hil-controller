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


@pytest.mark.asyncio
async def test_deploy_injects_pat_into_clone_url(mock_transport):
    adapter = GitDeployAdapter(
        transport=mock_transport,
        job_id="job-pat",
        source={
            "repo": "https://github.com/adafruit/Wippersnapper_Python.git",
            "ref": "main",
            "pat": "ghp_testtoken123",
        },
        params={},
    )
    await adapter.deploy()
    all_args = [arg for call in mock_transport.exec.call_args_list for arg in call.args[0]]
    clone_url = next((a for a in all_args if "github.com" in a), None)
    assert clone_url is not None
    assert "ghp_testtoken123@github.com" in clone_url


@pytest.mark.asyncio
async def test_run_stores_stdout_and_stderr(git_deploy, mock_transport):
    mock_transport.exec.return_value = make_exec_result(0, stdout="2 passed\n", stderr="warnings\n")
    await git_deploy.run()
    assert git_deploy._run_stdout == "2 passed\n"
    assert git_deploy._run_stderr == "warnings\n"


@pytest.mark.asyncio
async def test_deploy_stores_clone_stderr(mock_transport):
    adapter = GitDeployAdapter(
        transport=mock_transport,
        job_id="job-stderr",
        source={"repo": "https://github.com/adafruit/Wippersnapper_Python.git", "ref": "main"},
        params={},
    )
    mock_transport.exec.return_value = make_exec_result(0, stderr="Cloning into...\n")
    await adapter.deploy()
    assert "Cloning" in adapter._deploy_stderr


@pytest.mark.asyncio
async def test_deploy_stores_setup_stdout(mock_transport):
    adapter = GitDeployAdapter(
        transport=mock_transport,
        job_id="job-setup",
        source={
            "repo": "https://github.com/adafruit/Wippersnapper_Python.git",
            "ref": "main",
            "setup": ["pip", "install", "-e", ".[test]"],
        },
        params={},
    )
    mock_transport.exec.return_value = make_exec_result(0, stdout="Successfully installed\n")
    await adapter.deploy()
    assert "Successfully installed" in adapter._deploy_stdout


@pytest.mark.asyncio
async def test_deploy_no_pat_uses_plain_url(mock_transport):
    adapter = GitDeployAdapter(
        transport=mock_transport,
        job_id="job-nopat",
        source={
            "repo": "https://github.com/adafruit/Wippersnapper_Python.git",
            "ref": "main",
        },
        params={},
    )
    await adapter.deploy()
    all_args = [arg for call in mock_transport.exec.call_args_list for arg in call.args[0]]
    clone_url = next((a for a in all_args if "github.com" in a), None)
    assert clone_url == "https://github.com/adafruit/Wippersnapper_Python.git"
