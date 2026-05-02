"""Tests for claude-sandbox."""

import importlib.machinery
import importlib.util
import json
import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the script as a module
_loader = importlib.machinery.SourceFileLoader("claude_sandbox", str(Path(__file__).parent / "claude-sandbox"))
_spec = importlib.util.spec_from_file_location("claude_sandbox", loader=_loader)
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


class TestBindModeAndRegistry:
    def test_add_no_collision(self):
        reg = cs.MountRegistry()
        reg.add("/usr", "/usr", cs.BindMode.RO, "system")
        assert len(reg.entries) == 1
        assert reg.collisions == []

    def test_add_same_mode_no_collision(self):
        reg = cs.MountRegistry()
        reg.add("/usr", "/usr", cs.BindMode.RO, "system")
        reg.add("/usr", "/usr", cs.BindMode.RO, "global")
        assert len(reg.entries) == 1
        assert reg.collisions == []

    def test_add_different_mode_records_collision(self):
        reg = cs.MountRegistry()
        reg.add("/data", "/data", cs.BindMode.RO, "mcp:analyzer")
        reg.add("/data", "/data", cs.BindMode.RW, "project")
        assert len(reg.collisions) == 1
        assert "mcp:analyzer" in reg.collisions[0]
        assert "project" in reg.collisions[0]

    def test_later_layer_wins(self):
        reg = cs.MountRegistry()
        reg.add("/data", "/data", cs.BindMode.RO, "mcp:analyzer")
        reg.add("/data", "/data", cs.BindMode.RW, "project")
        assert reg.entries["/data"].mode == cs.BindMode.RW
        assert reg.entries["/data"].origin == "project"

    def test_to_bwrap_args_ro(self):
        reg = cs.MountRegistry()
        reg.add("/usr", "/usr", cs.BindMode.RO, "system")
        args = reg.to_bwrap_args()
        assert args == ["--ro-bind", "/usr", "/usr"]

    def test_to_bwrap_args_rw(self):
        reg = cs.MountRegistry()
        reg.add("/data", "/data", cs.BindMode.RW, "project")
        args = reg.to_bwrap_args()
        assert args == ["--bind", "/data", "/data"]

    def test_to_bwrap_args_null(self):
        reg = cs.MountRegistry()
        reg.add("/dev/null", "/project/.env", cs.BindMode.NULL, "project")
        args = reg.to_bwrap_args()
        assert args == ["--ro-bind", "/dev/null", "/project/.env"]


class TestParseSandboxConfig:
    def test_empty_config(self):
        config = cs.parse_sandbox_config({})
        assert config.network is True
        assert config.share_pid is False
        assert config.null_files == [".env", ".env.local", ".env.production"]
        assert config.mcp == []

    def test_override_network(self):
        config = cs.parse_sandbox_config({"network": False})
        assert config.network is False

    def test_override_share_pid(self):
        config = cs.parse_sandbox_config({"share_pid": True})
        assert config.share_pid is True

    def test_list_fields(self):
        config = cs.parse_sandbox_config({
            "ro_bind": ["/opt/tools"],
            "rw_bind": ["/data/out"],
            "null_files": [".env", ".secret"],
            "pass_env": ["AWS_*"],
            "mcp": ["ghidra"],
        })
        assert config.ro_bind == ["/opt/tools"]
        assert config.rw_bind == ["/data/out"]
        assert config.null_files == [".env", ".secret"]
        assert config.pass_env == ["AWS_*"]
        assert config.mcp == ["ghidra"]

    def test_scalar_coerced_to_list(self):
        config = cs.parse_sandbox_config({"mcp": "single-mcp"})
        assert config.mcp == ["single-mcp"]


class TestParseMcpDefinitions:
    def test_no_mcp_key(self):
        assert cs.parse_mcp_definitions({}) == {}

    def test_valid_mcp(self):
        raw = {
            "mcp": [
                {"name": "ghidra", "ro_bind": ["/opt/ghidra"], "pass_env": ["GHIDRA_*"]},
            ]
        }
        mcps = cs.parse_mcp_definitions(raw)
        assert "ghidra" in mcps
        assert mcps["ghidra"].ro_bind == ["/opt/ghidra"]
        assert mcps["ghidra"].pass_env == ["GHIDRA_*"]

    def test_entry_without_name_skipped(self):
        raw = {"mcp": [{"ro_bind": ["/something"]}]}
        assert cs.parse_mcp_definitions(raw) == {}

    def test_non_dict_entry_skipped(self):
        raw = {"mcp": ["not-a-dict"]}
        assert cs.parse_mcp_definitions(raw) == {}


class TestResolvePath:
    def test_expands_tilde(self):
        result = cs.resolve_path("~/foo")
        assert not result.startswith("~")
        assert result.endswith("/foo")

    def test_normalizes_dotdot(self):
        result = cs.resolve_path("/a/b/../c")
        assert result == "/a/c"


class TestIsUnderSystemBind:
    def test_under_usr(self):
        assert cs.is_under_system_bind("/usr/bin/node") is True

    def test_exact_match(self):
        assert cs.is_under_system_bind("/usr") is True

    def test_not_under_system(self):
        assert cs.is_under_system_bind("/home/user/bin/tool") is False

    def test_prefix_not_dir(self):
        assert cs.is_under_system_bind("/usr_local/bin") is False


class TestResolveEnvGlobs:
    def test_exact_match(self):
        with patch.dict(os.environ, {"FOO": "bar"}, clear=True):
            result = cs.resolve_env_globs(["FOO"])
            assert result == {"FOO"}

    def test_glob_match(self):
        with patch.dict(os.environ, {"AWS_KEY": "x", "AWS_SECRET": "y", "HOME": "z"}, clear=True):
            result = cs.resolve_env_globs(["AWS_*"])
            assert result == {"AWS_KEY", "AWS_SECRET"}

    def test_no_match(self):
        with patch.dict(os.environ, {"HOME": "/home"}, clear=True):
            result = cs.resolve_env_globs(["NONEXISTENT_*"])
            assert result == set()


class TestCreateFilteredClaudeConfig:
    def test_strips_disabled_mcps(self):
        config = {
            "mcpServers": {
                "ghidra": {"command": "ghidra-mcp"},
                "oracle": {"command": "oracle-mcp"},
                "secret": {"command": "secret-mcp"},
            },
            "otherKey": "preserved",
        }
        path = cs.create_filtered_claude_config(config, ["ghidra", "oracle"], verbose=False)
        try:
            with open(path) as f:
                result = json.load(f)
            assert set(result["mcpServers"].keys()) == {"ghidra", "oracle"}
            assert result["otherKey"] == "preserved"
        finally:
            os.unlink(path)

    def test_no_enabled_mcps_strips_all(self):
        config = {"mcpServers": {"ghidra": {"command": "x"}}}
        path = cs.create_filtered_claude_config(config, [], verbose=False)
        try:
            with open(path) as f:
                result = json.load(f)
            assert result["mcpServers"] == {}
        finally:
            os.unlink(path)


class TestNetworkAndPidOverride:
    """Project config should override global config for network and share_pid."""

    def _build_args(self, global_network, global_pid, project_network, project_pid, has_project):
        global_config = cs.SandboxConfig(network=global_network, share_pid=global_pid)
        project_config = cs.SandboxConfig(network=project_network, share_pid=project_pid)

        with patch.object(cs, "resolve_claude_binary", return_value=("/usr/bin/claude", ["/usr/bin"])):
            args = cs.build_bwrap_command(
                project_dir=Path("/tmp/test-project"),
                global_config=global_config,
                global_mcps={},
                project_config=project_config,
                project_raw_provided=has_project,
                enabled_mcps=[],
                claude_config={},
                claude_args=[],
                verbose=False,
                filtered_config_path=None,
            )
        return args

    def test_project_enables_network_over_global(self):
        args = self._build_args(
            global_network=False, global_pid=False,
            project_network=True, project_pid=False,
            has_project=True,
        )
        assert "--share-net" in args
        assert "--unshare-net" not in args

    def test_project_disables_network_over_global(self):
        args = self._build_args(
            global_network=True, global_pid=False,
            project_network=False, project_pid=False,
            has_project=True,
        )
        assert "--unshare-net" in args
        assert "--share-net" not in args

    def test_project_enables_pid_sharing_over_global(self):
        args = self._build_args(
            global_network=True, global_pid=False,
            project_network=True, project_pid=True,
            has_project=True,
        )
        assert "--unshare-pid" not in args

    def test_project_disables_pid_sharing_over_global(self):
        args = self._build_args(
            global_network=True, global_pid=True,
            project_network=True, project_pid=False,
            has_project=True,
        )
        assert "--unshare-pid" in args

    def test_no_project_config_uses_global(self):
        args = self._build_args(
            global_network=False, global_pid=True,
            project_network=True, project_pid=False,
            has_project=False,
        )
        assert "--unshare-net" in args
        assert "--unshare-pid" not in args
