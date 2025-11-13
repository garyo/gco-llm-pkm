# Google Calendar Integration Setup Guide

Complete guide for setting up Google Calendar integration with your PKM Bridge Server.

## Prerequisites

- Google account
- Access to Google Cloud Console
- Server running on localhost:8000 (dev) or your production domain

## Step-by-Step Setup

### 1. Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click "Select a project" → "New Project"
3. Enter project name (e.g., "PKM Bridge")
4. Click "Create"

### 2. Enable Google Calendar API

1. In the Google Cloud Console, select your project
2. Navigate to "APIs & Services" → "Library"
3. Search for "Google Calendar API"
4. Click on it and press "Enable"

### 3. Configure OAuth Consent Screen

1. Go to "APIs & Services" → "OAuth consent screen"
2. Select "External" (unless you have a Google Workspace)
3. Click "Create"
4. Fill in the required fields:
   - **App name**: PKM Bridge (or your preferred name)
   - **User support email**: Your email
   - **Developer contact email**: Your email
5. Click "Save and Continue"
6. Skip "Scopes" for now (click "Save and Continue")
7. Add test users (your own email) if in testing mode
8. Click "Save and Continue"
9. Review and click "Back to Dashboard"

### 4. Create OAuth 2.0 Credentials

1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth client ID"
3. Select "Web application"
4. Configure:
   - **Name**: PKM Bridge Web Client
   - **Authorized JavaScript origins**: Leave empty
   - **Authorized redirect URIs**: Add both:
     - `http://localhost:8000/auth/google-calendar/callback`
     - `https://pkm.oberbrunner.com/auth/google-calendar/callback` (replace with your domain)
5. Click "Create"
6. **Important**: Copy your Client ID and Client Secret

### 5. Configure Your Application

1. Copy `.env.example` to `.env` (if you haven't already)
2. Add your Google Calendar credentials:

```bash
# Google Calendar Integration
GOOGLE_CLIENT_ID=123456789-abcdefghijklmnop.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret-here
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google-calendar/callback
```

**For production**, update the redirect URI:
```bash
GOOGLE_REDIRECT_URI=https://pkm.oberbrunner.com/auth/google-calendar/callback
```

### 6. Connect Your Calendar

1. Start the server:
   ```bash
   ./pkm-bridge-server.py
   ```

2. Open your browser and visit:
   ```
   http://localhost:8000/auth/google-calendar/authorize
   ```

3. You'll be redirected to Google's authorization page:
   - Select your Google account
   - Review the permissions requested
   - Click "Allow"

4. You'll be redirected back with a success message

5. Verify connection:
   ```bash
   curl http://localhost:8000/auth/google-calendar/status
   ```

## Usage Examples

Once connected, you can use natural language to interact with your calendar:

### Query Calendar
```
You: What's on my calendar today?
Assistant: [Shows today's events with times and locations]

You: Show me this week's meetings
Assistant: [Lists all events for the next 7 days]

You: Find all events about PKM
Assistant: [Searches and displays matching events]
```

### Create Events
```
You: Add a meeting with John tomorrow at 2pm for 1 hour
Assistant: ✓ Created event: Meeting with John on 2025-11-14 14:00

You: Create an event: Team standup tomorrow 9am-9:30am
Assistant: ✓ Created event: Team standup on 2025-11-14 09:00

You: Quick add: Dentist appointment next Tuesday 3pm
Assistant: ✓ Created event: Dentist appointment
```

### Update Events
```
You: Move my meeting with John to 3pm
Assistant: [Updates the event time]
```

### Search
```
You: When is my next dentist appointment?
Assistant: [Searches for and displays dentist appointments]
```

## Troubleshooting

### "Google Calendar not configured" Error

**Cause**: Environment variables not set or server needs restart

**Solution**:
1. Check `.env` file has all three variables set
2. Restart the server
3. Look for "Google Calendar OAuth handler initialized" in logs

### "Invalid redirect_uri" Error

**Cause**: Redirect URI mismatch between `.env` and Google Cloud Console

**Solution**:
1. Check exact URI in `.env` matches one in Google Cloud Console
2. Common mistakes:
   - Missing `/auth/google-calendar/callback` path
   - HTTP vs HTTPS mismatch
   - Trailing slash differences
3. Update Google Cloud Console credentials if needed

### "Access blocked: This app hasn't been verified"

**Cause**: OAuth consent screen not published

**Solution**:
1. Add your email as a test user in OAuth consent screen
2. OR publish your app (only needed for public use)

### Connection Lost / Token Expired

**Cause**: OAuth token expired (tokens last ~1 hour, refresh tokens persist)

**Solution**:
- The server automatically refreshes tokens
- If refresh fails, reconnect: visit `/auth/google-calendar/authorize`

### "403 Forbidden" Errors

**Cause**: Google Calendar API not enabled or insufficient permissions

**Solution**:
1. Verify Google Calendar API is enabled in Google Cloud Console
2. Check that scopes include calendar read/write permissions
3. Try disconnecting and reconnecting

## Security Considerations

### Token Storage

- OAuth tokens are stored securely in PostgreSQL database
- Access tokens expire after ~1 hour
- Refresh tokens persist and are used to get new access tokens
- Database should be properly secured in production

### Permissions

The integration requests these scopes:
- `https://www.googleapis.com/auth/calendar` - Full calendar access
- `https://www.googleapis.com/auth/calendar.events` - Events access

### Development vs Production

**Development** (localhost):
- Use `http://localhost:8000/auth/google-calendar/callback`
- No HTTPS required for localhost
- Suitable for testing

**Production**:
- Use `https://your-domain.com/auth/google-calendar/callback`
- HTTPS required (except localhost)
- Same OAuth client works for both!

## Advanced Configuration

### Multiple Calendars

By default, the integration uses your "primary" calendar. To access other calendars:

```python
# In the tool, specify calendar_id parameter
calendar_id = "your-calendar-id@group.calendar.google.com"
```

### Timezone Configuration

Events are created in UTC by default. To use a different timezone:

```python
# When creating events
timezone = "America/New_York"  # or "Europe/London", etc.
```

### Custom Scopes

If you need different permissions, edit `pkm_bridge/google_oauth.py`:

```python
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",  # Read-only
    # or
    "https://www.googleapis.com/auth/calendar",  # Full access
]
```

## Disconnecting

To disconnect Google Calendar:

1. Via API:
   ```bash
   curl -X POST http://localhost:8000/auth/google-calendar/disconnect
   ```

2. Or manually delete tokens from database:
   ```sql
   DELETE FROM oauth_tokens WHERE service = 'google_calendar';
   ```

3. To fully revoke access, also visit:
   [Google Account Permissions](https://myaccount.google.com/permissions)

## Testing

Run the integration test:

```bash
uv run python3 test-google-calendar.py
```

This verifies:
- ✓ All modules import correctly
- ✓ OAuth handler initializes
- ✓ Tool schema is properly configured
- ✓ Authorization URLs generate correctly

## Support

For issues or questions:
- Check server logs for detailed error messages
- Review [Google Calendar API documentation](https://developers.google.com/calendar/api/guides/overview)
- See `README.md` troubleshooting section
- File an issue on GitHub (when public)
