"""Tests for agent_api.workspace — file sync and path safety."""

import pytest
from unittest.mock import AsyncMock, patch

from agent_api import workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_exec(responses=None):
    """Patch workspace._exec to return canned (returncode, output) tuples."""
    call_log = []
    idx = [0]

    async def fake_exec(container, cmd, timeout=120):
        call_log.append((container, cmd))
        if responses and idx[0] < len(responses):
            result = responses[idx[0]]
            idx[0] += 1
            return result
        return (0, "")

    return fake_exec, call_log


# ---------------------------------------------------------------------------
# sync_to_container
# ---------------------------------------------------------------------------

class TestSyncToContainer:
    @pytest.mark.asyncio
    async def test_files_written_to_correct_path(self):
        fake, calls = _mock_exec([(0, ""), (0, "")] * 5)  # enough responses

        with patch.object(workspace, "_exec", side_effect=fake):
            ok = await workspace.sync_to_container(
                "ctr-1", "/workspace",
                {"src/main.py": "print('hello')", "README.md": "# Hi"},
            )

        assert ok is True
        # Should have mkdir + write for src/main.py, and write for README.md
        cmds = [c[1] for c in calls]
        assert any("mkdir -p" in c and "/workspace/src" in c for c in cmds)
        assert any("base64 -d > /workspace/src/main.py" in c for c in cmds)
        assert any("base64 -d > /workspace/README.md" in c for c in cmds)

    @pytest.mark.asyncio
    async def test_write_failure_returns_false(self):
        fake, _ = _mock_exec([(0, ""), (1, "permission denied")])

        with patch.object(workspace, "_exec", side_effect=fake):
            ok = await workspace.sync_to_container(
                "ctr-1", "/workspace", {"fail.txt": "data"},
            )

        assert ok is False


# ---------------------------------------------------------------------------
# sync_from_container
# ---------------------------------------------------------------------------

class TestSyncFromContainer:
    @pytest.mark.asyncio
    async def test_returns_dict_of_files(self):
        listing = "./src/main.py\n./README.md"
        responses = [
            (0, listing),           # find
            (0, "print('hello')"),  # cat src/main.py
            (0, "# Hi"),           # cat README.md
        ]
        fake, _ = _mock_exec(responses)

        with patch.object(workspace, "_exec", side_effect=fake):
            files = await workspace.sync_from_container("ctr-1", "/workspace")

        # Keys are relative paths (workspace prefix stripped)
        assert len(files) == 2

    @pytest.mark.asyncio
    async def test_empty_workspace_returns_empty(self):
        fake, _ = _mock_exec([(0, "")])  # find returns nothing

        with patch.object(workspace, "_exec", side_effect=fake):
            files = await workspace.sync_from_container("ctr-1", "/workspace")

        assert files == {}

    @pytest.mark.asyncio
    async def test_find_failure_returns_empty(self):
        fake, _ = _mock_exec([(1, "error")])

        with patch.object(workspace, "_exec", side_effect=fake):
            files = await workspace.sync_from_container("ctr-1", "/workspace")

        assert files == {}


# ---------------------------------------------------------------------------
# Path traversal (tested via main._validate_path)
# ---------------------------------------------------------------------------

class TestPathValidation:
    """Test the _validate_path function from main.py that guards workspace endpoints."""

    def test_path_traversal_rejected(self):
        from agent_api.main import _validate_path
        from fastapi import HTTPException

        bad_paths = [
            "../etc/passwd",
            "/absolute/path",
            "file/../../../escape",
            "foo/../../bar",
            "",
            "file\x00name",
        ]
        for p in bad_paths:
            with pytest.raises(HTTPException) as exc_info:
                _validate_path(p)
            assert exc_info.value.status_code == 400, f"Expected 400 for path: {p!r}"

    def test_valid_paths_accepted(self):
        from agent_api.main import _validate_path

        good_paths = [
            "file.txt",
            "src/main.py",
            "deep/nested/dir/file.json",
            "kebab-case.txt",
            "under_score.py",
        ]
        for p in good_paths:
            result = _validate_path(p)
            assert result == p


# ---------------------------------------------------------------------------
# Large file doesn't crash
# ---------------------------------------------------------------------------

class TestLargeFile:
    @pytest.mark.asyncio
    async def test_large_file_sync(self):
        large_content = "x" * (10 * 1024 * 1024)  # 10 MB
        fake, _ = _mock_exec([(0, "")] * 5)

        with patch.object(workspace, "_exec", side_effect=fake):
            ok = await workspace.sync_to_container(
                "ctr-1", "/workspace", {"big.bin": large_content},
            )

        assert ok is True
