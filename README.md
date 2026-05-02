# claude-sandbox

Run [Claude Code](https://claude.ai/claude-code) with `--dangerously-skip-permissions` safely, inside a [bubblewrap](https://github.com/containers/bubblewrap) sandbox.

Claude gets full write access to your project directory and its own config, but nothing else. Secrets files are nulled out, MCP servers are filtered to only those you explicitly enable, and the `.sandbox` config itself is read-only so Claude can't modify its own constraints.

## Requirements

- Linux
- Python 3.11+ (uses `tomllib`)
- [bubblewrap](https://github.com/containers/bubblewrap) (`bwrap`)
- Claude Code CLI

## Install

```bash
# Clone and symlink into PATH
git clone https://github.com/youruser/claude-sandbox.git
ln -s "$(pwd)/claude-sandbox/claude-sandbox" ~/.local/bin/claude-sandbox
```

## Usage

```bash
cd /path/to/project
claude-sandbox                          # interactive session
claude-sandbox -p "fix the login bug"   # one-shot prompt
claude-sandbox --verbose                # show all mounts and collisions
claude-sandbox --dry-run                # print the bwrap command without running
```

All arguments (except `--verbose` and `--dry-run`) are passed through to `claude`.

## What the sandbox does

| Resource | Access | Notes |
|---|---|---|
| `/usr`, `/bin`, `/lib`, `/lib64` | read-only | System binaries and libraries |
| `/etc/resolv.conf`, `/etc/hosts`, `/etc/ssl` | read-only | DNS and TLS |
| `~/.gitconfig`, `~/.nvm` | read-only | Git config and node versions |
| `~/.claude`, `~/.claude.json` | read-write | Claude's own config (filtered copy) |
| Project directory (pwd) | read-write | The code you're working on |
| `.env`, `.env.local`, `.env.production` | nulled | Replaced with `/dev/null` |
| `.sandbox` | read-only | Prevents Claude from editing its own constraints |
| `/tmp` | tmpfs | Isolated temp directory |
| Network | on by default | Configurable |
| PID namespace | isolated | Configurable |
| MCP servers | none by default | Must be explicitly enabled |

## Configuration

There are two config files, both TOML. Settings are applied in layers, with later layers overriding earlier ones:

```
system ro-binds -> claude binary -> claude config -> MCP definitions -> global config -> project config
```

### `~/.claude-sandbox` (global)

Defines MCP server resource requirements and global defaults. Applies to all projects.

```toml
# Global defaults
network = true
share_pid = false
pass_env = ["ANTHROPIC_*"]

# MCP server definitions
# Declare what each MCP server needs to function.
# These are only activated when a project enables the MCP by name.
[[mcp]]
name = "ghidra"
ro_bind = ["/home/user/ghidra"]
rw_bind = []
pass_env = ["GHIDRA_*"]

[[mcp]]
name = "mcp-oracle"
ro_bind = ["/home/user/code/mcp-oracle"]
rw_bind = []
pass_env = []
```

### `.sandbox` (per-project)

Place at the root of your project directory. Overrides global config.

```toml
# Sandbox options
network = true
share_pid = false

# Which MCPs to enable (references ~/.claude-sandbox definitions)
mcp = ["ghidra"]

# Extra paths (beyond the project dir)
ro_bind = ["/data/shared-assets"]
rw_bind = ["/data/output"]

# Files inside the project to replace with /dev/null
null_files = [".env", ".env.local", ".env.production"]

# Environment variables to pass in (supports globs)
pass_env = ["GITHUB_*", "SSH_AUTH_SOCK"]
```

### Config reference

| Key | Type | Default | Description |
|---|---|---|---|
| `network` | bool | `true` | Enable network access |
| `share_pid` | bool | `false` | Share host PID namespace |
| `ro_bind` | list[str] | `[]` | Extra read-only bind mounts |
| `rw_bind` | list[str] | `[]` | Extra read-write bind mounts |
| `null_files` | list[str] | `[".env", ...]` | Project files to replace with `/dev/null` |
| `pass_env` | list[str] | `[]` | Env var patterns to forward (glob syntax) |
| `mcp` | list[str] | `[]` | MCP servers to enable by name |

## MCP filtering

By default, **no MCP servers are started**. Claude sees a filtered copy of `~/.claude.json` with `mcpServers` stripped down to only the names listed in `mcp`.

To enable MCPs:

1. Define their resource needs in `~/.claude-sandbox` with `[[mcp]]` blocks
2. Enable them per-project in `.sandbox` with `mcp = ["name"]`

The script auto-detects filesystem paths from each MCP's command and args in `~/.claude.json` and binds them read-only. The `[[mcp]]` definition in `~/.claude-sandbox` adds any additional paths the MCP needs beyond what's auto-detected.

## Mount collision handling

When multiple layers bind the same path with different modes (e.g., an MCP binds a path read-only but the project needs it read-write), the **later layer wins** -- this is intentional, since the project developer knows their context best.

Collisions are reported:
- `--verbose`: full details of every mode change with origin labels
- Without `--verbose`: a one-line hint if mode-changing collisions exist

```
$ claude-sandbox --verbose
verbose: mount collisions (later layer wins):
  /home/user/data: ro (mcp:analyzer) -> rw (project)
```

## Examples

**Minimal -- just sandbox the project, no MCPs:**
```bash
cd ~/code/my-project
claude-sandbox
```

**With MCPs and extra paths:**
```toml
# ~/code/my-project/.sandbox
mcp = ["ghidra"]
ro_bind = ["/opt/toolchain"]
pass_env = ["AWS_*"]
```

**No network (air-gapped analysis):**
```toml
# .sandbox
network = false
mcp = []
```

**Inspect what would run:**
```bash
claude-sandbox --dry-run --verbose -p "review the code"
```

## License

MIT
