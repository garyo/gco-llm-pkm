# CLI Authentication Guide

The `pkm-cli.py` client now supports authentication when the server has `AUTH_ENABLED=true`.

## How It Works

The CLI client handles authentication automatically:

1. **First run**: Prompts for password
2. **Saves token**: Stores JWT token in `~/.pkm-cli-token` (600 permissions)
3. **Subsequent runs**: Uses saved token (valid for 1 week)
4. **Token expiry**: Automatically re-authenticates when token expires

## Usage Methods

### Method 1: Interactive Authentication (Default)

Just run the CLI - it will prompt for your password on first use:

```bash
./pkm-cli.py "What did I write about music?"
```

**First time:**
```
Authentication required.
Password: ********
✓ Authenticated successfully
[Your query results...]
```

**Subsequent runs:**
```
[Your query results...]
```

The token is saved to `~/.pkm-cli-token` and reused automatically.

### Method 2: Environment Variable (Automated)

Set your password in `.env` for completely automated authentication:

```bash
# In your .env file
PKM_PASSWORD=your-password-here
```

Now the CLI never prompts - it authenticates automatically when needed:

```bash
./pkm-cli.py "List my org files"
# No password prompt!
```

**Use cases:**
- Scripts and automation
- Cron jobs
- CI/CD pipelines
- When you don't want to be prompted

### Method 3: Disable Auth (Development)

For local development, you can disable auth entirely:

```bash
# In your .env file
AUTH_ENABLED=false
```

The CLI will work without any authentication.

## Token Management

### Where is the token stored?

```bash
~/.pkm-cli-token
```

This file contains your JWT token and is set to 600 permissions (readable only by you).

### How long is the token valid?

Default: 1 week (168 hours)

Configurable via `TOKEN_EXPIRY_HOURS` in `.env`.

### What happens when the token expires?

The CLI automatically:
1. Detects the 401 error
2. Prompts for password (or uses `PKM_PASSWORD` if set)
3. Gets a new token
4. Retries the request

### How do I manually clear the token?

```bash
rm ~/.pkm-cli-token
```

Next run will prompt for password again.

### How do I check if my token is valid?

```bash
./pkm-cli.py --health
```

If the token is invalid, you'll be prompted to re-authenticate.

## REPL Mode

Authentication works seamlessly in interactive REPL mode:

```bash
./pkm-cli.py
```

**First time:**
```
Authentication required.
Password: ********
✓ Authenticated successfully
============================================================
PKM Assistant - Interactive Mode
============================================================
...

You: What did I write about sailing?
```

**Subsequent runs:**
```
============================================================
PKM Assistant - Interactive Mode
============================================================
...

You: What did I write about sailing?
```

## Security Considerations

### Token File Security

The token file is automatically set to 600 permissions:
```bash
ls -la ~/.pkm-cli-token
-rw------- 1 user user 245 Oct 26 18:00 /home/user/.pkm-cli-token
```

Only you can read it.

### Environment Variable Security

If using `PKM_PASSWORD` in `.env`:

**DO:**
- ✅ Keep `.env` in `.gitignore`
- ✅ Use 600 permissions: `chmod 600 .env`
- ✅ Use a strong, unique password

**DON'T:**
- ❌ Commit `.env` to git
- ❌ Share your `.env` file
- ❌ Use the same password as other services

### Shared Machines

If using a shared machine, consider:
- **Don't use `PKM_PASSWORD`** - anyone with access to `.env` can see it
- **Use interactive auth** - token is in your home directory only
- **Clear token when done**: `rm ~/.pkm-cli-token`

## Troubleshooting

### "Authentication failed: Invalid password"

Your password is incorrect. Double-check:
1. You're using the password you set with `./generate-auth-config.py`
2. The `PASSWORD_HASH` in `.env` matches your password
3. You restarted the server after changing `.env`

### "Too many login attempts"

You've exceeded the rate limit (5 attempts per minute). Wait 60 seconds and try again.

### "Token expired. Re-authenticating..."

Normal behavior when your token expires. The CLI will prompt for password and get a new token.

### "Connection error"

Server isn't running or is on a different host/port. Check:
```bash
# Is the server running?
ps aux | grep pkm-bridge-server

# Check server health
curl http://localhost:8000/health
```

### Permission denied on token file

```bash
chmod 600 ~/.pkm-cli-token
```

## Examples

### One-off query
```bash
./pkm-cli.py "What are my active TODOs?"
```

### With custom model
```bash
./pkm-cli.py -m claude-sonnet-4-5 "Analyze my journal entries from last month"
```

### Check server health
```bash
./pkm-cli.py --health
```

### Interactive REPL
```bash
./pkm-cli.py
# Then type queries interactively
```

### In a script
```bash
#!/bin/bash
# Make sure PKM_PASSWORD is in .env
export $(cat .env | grep PKM_PASSWORD)

./pkm-cli.py "What did I work on today?" >> daily-summary.txt
```

## Automation Example

### Daily digest cron job

```bash
# Add to crontab: crontab -e
0 9 * * * cd /path/to/gco-pkm-llm && ./pkm-cli.py "Summarize yesterday's work" | mail -s "Daily PKM Digest" you@example.com
```

Make sure `PKM_PASSWORD` is set in `.env` for unattended operation.

## Comparison: Web UI vs CLI

| Feature | Web UI | CLI |
|---------|--------|-----|
| Authentication | Login page | Token file + prompt |
| Token storage | localStorage | `~/.pkm-cli-token` |
| Token lifespan | 1 week | 1 week |
| Automation | No | Yes (with `PKM_PASSWORD`) |
| Interactive | Yes | Yes (REPL mode) |
| Session history | Yes | Yes |

Both use the same JWT tokens and authentication system!
