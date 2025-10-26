# Authentication Setup Guide

Your PKM Bridge Server now supports JWT-based authentication to protect your personal knowledge base.

## Quick Start

### 1. Generate Auth Credentials

Run the helper script to generate secure credentials:

```bash
./generate-auth-config.py
```

**Note**: This script uses `uv` with PEP-723 inline dependencies, so bcrypt will be automatically installed when you run it.

This will:
- Generate a secure JWT secret key
- Prompt you to create a password
- Hash your password with bcrypt (12 rounds)
- Display the configuration to add to your `.env` file

### 2. Update .env File

Add the generated configuration to your `.env` file:

```bash
AUTH_ENABLED=true
JWT_SECRET=<generated-secret-from-script>
PASSWORD_HASH=<generated-hash-from-script>
TOKEN_EXPIRY_HOURS=168  # 1 week
```

### 3. Restart the Server

```bash
./pkm-bridge-server.py
```

The server will now require authentication. You'll see a login page when you visit http://localhost:8000.

## How It Works

### Login Flow

1. Visit http://localhost:8000
2. Enter your password on the login page
3. Receive a JWT token (valid for 1 week by default)
4. Token is stored in browser localStorage
5. All API requests include the token in the Authorization header

### Token Expiration

- Tokens expire after `TOKEN_EXPIRY_HOURS` (default: 168 hours = 1 week)
- When a token expires, you'll be redirected to the login page
- Simply log in again to get a fresh token

### Logout

Click the "Logout" button in the bottom-left controls to:
- Clear your token from localStorage
- Return to the login page

## Security Features

✅ **Password hashing**: Passwords are bcrypt hashed (12 rounds), never stored in plain text
✅ **JWT tokens**: Industry-standard JSON Web Tokens for authentication
✅ **Rate limiting**: Login attempts limited to 5 per minute to prevent brute-force attacks
✅ **Audit logging**: All authentication events are logged with IP addresses
✅ **Protected endpoints**: All sensitive API endpoints require authentication
✅ **Token expiration**: Tokens automatically expire after configured time
✅ **Secure storage**: Tokens stored in browser localStorage (HTTPS recommended for production)

## Disabling Authentication

To disable authentication temporarily:

1. Edit `.env` file:
   ```bash
   AUTH_ENABLED=false
   ```

2. Restart the server

The login page will be bypassed and all endpoints will be accessible without authentication.

## Production Deployment

For production use with Tailscale or public deployment:

### With Tailscale (Recommended)

If you're using Tailscale, you have two options:

1. **Tailscale only** - Set `AUTH_ENABLED=false` and rely on Tailscale's network-level security
2. **Tailscale + JWT auth** - Keep `AUTH_ENABLED=true` for defense in depth

### Public Deployment

If deploying publicly:

1. **Always use HTTPS** - JWT tokens sent over HTTP can be intercepted
2. **Enable authentication** - Set `AUTH_ENABLED=true`
3. **Use a strong password** - At least 12 characters, mix of letters/numbers/symbols
4. **Consider shorter token expiry** - Maybe 24 hours instead of 1 week
5. **Monitor logs** - Check for failed login attempts

## Changing Your Password

1. Run `./generate-auth-config.py` again with your new password
2. Copy the new `PASSWORD_HASH` value
3. Update `.env` file with the new hash
4. Restart the server
5. All existing tokens will remain valid until they expire

## Manual Configuration

If you prefer to generate credentials manually:

### Generate JWT Secret

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Hash Your Password

```bash
# Using uv (automatically installs bcrypt)
uv run --with bcrypt python3 -c "import bcrypt; password=b'your-password'; print(bcrypt.hashpw(password, bcrypt.gensalt(12)).decode())"
```

Then add to `.env`:

```bash
AUTH_ENABLED=true
JWT_SECRET=<your-generated-secret>
PASSWORD_HASH=<your-bcrypt-hash>
TOKEN_EXPIRY_HOURS=168
```

**Note**: bcrypt hashes start with `$2b$12$` and are approximately 60 characters long.

## Troubleshooting

### "JWT_SECRET must be set to a secure random value"

Make sure you've set a valid JWT_SECRET in your `.env` file. Don't use the default value from `.env.example`.

### "PASSWORD_HASH must be set"

You need to generate a password hash using `./generate-auth-config.py` or the manual method above.

### "Invalid password" on login

Double-check that:
1. You're using the correct password
2. The PASSWORD_HASH in `.env` matches your password
3. You restarted the server after changing `.env`

### Token expired immediately

Check your system clock. JWT tokens use UTC timestamps and won't work if your system time is incorrect.

## Rate Limiting

To prevent abuse, the following rate limits are enforced:

- **Login attempts**: 5 per minute (prevents brute-force attacks)
- **Token verification**: 30 per minute
- **Queries**: 60 per minute
- **History access**: 30 per minute
- **Session operations**: 10 per minute
- **Global default**: 200 requests per hour

If you exceed these limits, you'll receive a 429 (Too Many Requests) error. Wait a minute and try again.

## Audit Logging

All authentication events are logged with the following information:

**Successful login**:
```
✅ Successful login from 192.168.1.100
Generated token for 'user', expires at 2025-11-02T18:00:00Z
```

**Failed login**:
```
❌ Failed login attempt from 192.168.1.100 - invalid password
```

**Unauthorized access attempts**:
```
Unauthorized query attempt from 192.168.1.100: invalid token
```

**Token events**:
```
Token verification from 192.168.1.100: invalid/expired
```

Check your server logs to monitor for suspicious activity.

## Future Enhancements

Possible future authentication improvements:

- ✅ ~~bcrypt password hashing~~ (implemented!)
- ✅ ~~Rate limiting on login attempts~~ (implemented!)
- ✅ ~~Audit logging of authentication events~~ (implemented!)
- Multiple user accounts with database storage
- Argon2 password hashing (even more secure than bcrypt)
- 2FA support
- OAuth2 integration (Google, GitHub, etc.)
- Server-side token revocation/blacklist
- Account lockout after N failed attempts
- IP-based restrictions

For now, this JWT + bcrypt + rate limiting system provides strong security for single-user personal use, especially when combined with Tailscale.
