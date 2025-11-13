"""Google Calendar integration tool for Claude."""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import logging

from pkm_bridge.tools.base import BaseTool
from pkm_bridge.database import get_db
from pkm_bridge.db_repository import OAuthRepository
from pkm_bridge.google_oauth import GoogleOAuth
from pkm_bridge.google_calendar_client import GoogleCalendarClient


class GoogleCalendarTool(BaseTool):
    """Tool for querying and managing Google Calendar events."""

    def __init__(self, logger: logging.Logger, oauth_handler: Optional[GoogleOAuth] = None):
        """Initialize Google Calendar tool.

        Args:
            logger: Logger instance
            oauth_handler: Optional GoogleOAuth instance
        """
        super().__init__(logger)
        self.oauth_handler = oauth_handler

    @property
    def name(self) -> str:
        """Tool name for Claude API."""
        return "google_calendar"

    @property
    def description(self) -> str:
        """Tool description for Claude API."""
        return """Query and manage Google Calendar events. Use this to:
- List all available calendars (shows calendar IDs and access levels)
- List today's events or this week's events
- List events in a specific date range
- Create new calendar events
- Update existing events (requires event_id - use search first to get the ID)
- Delete events (requires event_id - use search first to get the ID)
- Search for events by keyword (returns event IDs in [ID: xxx] format)
- Quick add events using natural language

You have access to ALL calendars the user has granted permission to, not just the primary calendar.
Use list_calendars to see all available calendars, then use the calendar_id parameter to access specific calendars.

IMPORTANT: To update or delete an event, first search for it to get its event_id.
The search results include [ID: xxx] which is the event_id needed for updates/deletes.

Connection status: Check /auth/google-calendar/status. If not connected, user needs to visit /auth/google-calendar/authorize."""

    @property
    def input_schema(self) -> Dict[str, Any]:
        """JSON schema for tool input."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_calendars",
                        "list_today",
                        "list_week",
                        "list_range",
                        "create",
                        "update",
                        "delete",
                        "search",
                        "quick_add"
                    ],
                    "description": "Action to perform"
                },
                "summary": {
                    "type": "string",
                    "description": "Event title/summary (for create, update, or quick_add)"
                },
                "start": {
                    "type": "string",
                    "description": "Event start time in ISO format: YYYY-MM-DDTHH:MM:SS (for create, update)"
                },
                "end": {
                    "type": "string",
                    "description": "Event end time in ISO format: YYYY-MM-DDTHH:MM:SS (for create, update)"
                },
                "description": {
                    "type": "string",
                    "description": "Event description (optional, for create, update)"
                },
                "location": {
                    "type": "string",
                    "description": "Event location (optional, for create, update)"
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee email addresses (optional, for create)"
                },
                "event_id": {
                    "type": "string",
                    "description": "Event ID from Google Calendar (for update or delete). Get this from search results - look for [ID: xxx] in the output."
                },
                "query": {
                    "type": "string",
                    "description": "Search query or natural language text (for search or quick_add)"
                },
                "time_min": {
                    "type": "string",
                    "description": "Start of date range in ISO format YYYY-MM-DD (for list_range)"
                },
                "time_max": {
                    "type": "string",
                    "description": "End of date range in ISO format YYYY-MM-DD (for list_range)"
                },
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: 'primary')"
                },
                "timezone": {
                    "type": "string",
                    "description": "Timezone for event times (default: 'UTC'). Examples: 'America/New_York', 'Europe/London'"
                }
            },
            "required": ["action"]
        }

    def get_client(self) -> Optional[GoogleCalendarClient]:
        """Get authenticated Google Calendar client.

        Returns:
            GoogleCalendarClient if authenticated, None otherwise
        """
        if not self.oauth_handler:
            return None

        try:
            db = get_db()
            token = OAuthRepository.get_token(db, 'google_calendar')

            if not token:
                return None

            # Check if token needs refresh
            if OAuthRepository.is_token_expired(token):
                self.logger.info("Google Calendar token expired, refreshing...")
                try:
                    new_token_data = self.oauth_handler.refresh_token(token.refresh_token)

                    # Update token in database
                    OAuthRepository.save_token(
                        db=db,
                        service='google_calendar',
                        access_token=new_token_data['access_token'],
                        refresh_token=new_token_data.get('refresh_token'),
                        expires_at=new_token_data['expires_at'],
                        scope=new_token_data.get('scope')
                    )

                    token = OAuthRepository.get_token(db, 'google_calendar')
                    self.logger.info("Google Calendar token refreshed successfully")

                except Exception as e:
                    self.logger.error(f"Failed to refresh Google Calendar token: {e}")
                    return None
                finally:
                    db.close()
            else:
                db.close()

            return GoogleCalendarClient(token.access_token, token.refresh_token)

        except Exception as e:
            self.logger.error(f"Error getting Google Calendar client: {e}")
            return None

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        """Execute Google Calendar action.

        Args:
            params: Tool parameters with 'action' and action-specific params
            context: Optional execution context

        Returns:
            Execution result as string
        """
        action = params.get('action')
        if not action:
            return "Error: 'action' parameter is required"

        client = self.get_client()

        if not client:
            return "Google Calendar not connected. Please connect via /auth/google-calendar/authorize"

        try:
            calendar_id = params.get('calendar_id', 'primary')

            if action == "list_calendars":
                self.logger.info("Executing list_calendars action")
                calendars = client.list_calendars()
                self.logger.info(f"Found {len(calendars)} calendars")

                if not calendars:
                    return "No calendars found."

                # Format calendar list with useful information
                lines = [f"Available calendars ({len(calendars)}):"]
                for cal in calendars:
                    cal_id = cal.get('id', 'unknown')
                    summary = cal.get('summary', 'Untitled')
                    primary = " (PRIMARY)" if cal.get('primary', False) else ""
                    access_role = cal.get('accessRole', 'unknown')

                    lines.append(f"• {summary}{primary}")
                    lines.append(f"  ID: {cal_id}")
                    lines.append(f"  Access: {access_role}")

                return "\n".join(lines)

            elif action == "list_today":
                self.logger.info(f"Executing list_today action for calendar: {calendar_id}")
                events = client.get_today_events(calendar_id=calendar_id)
                self.logger.info(f"list_today returned {len(events)} events")

                if not events:
                    return "No events scheduled for today."

                summaries = [client.format_event_summary(e) for e in events]
                return f"Today's events ({len(events)}):\n" + "\n".join(f"• {s}" for s in summaries)

            elif action == "list_week":
                self.logger.info(f"Executing list_week action for calendar: {calendar_id}")
                events = client.get_week_events(calendar_id=calendar_id)
                self.logger.info(f"list_week returned {len(events)} events")

                if not events:
                    return "No events scheduled for the next 7 days."

                summaries = [client.format_event_summary(e) for e in events]
                return f"This week's events ({len(events)}):\n" + "\n".join(f"• {s}" for s in summaries)

            elif action == "list_range":
                time_min_str = params.get('time_min')
                time_max_str = params.get('time_max')

                if not time_min_str or not time_max_str:
                    return "Error: Both time_min and time_max are required for list_range"

                try:
                    time_min = datetime.fromisoformat(time_min_str)
                    time_max = datetime.fromisoformat(time_max_str)
                except ValueError as e:
                    return f"Error: Invalid date format: {e}"

                events = client.get_events(
                    calendar_id=calendar_id,
                    time_min=time_min,
                    time_max=time_max
                )

                if not events:
                    return f"No events found between {time_min_str} and {time_max_str}."

                summaries = [client.format_event_summary(e) for e in events]
                return f"Events ({len(events)}):\n" + "\n".join(f"• {s}" for s in summaries)

            elif action == "create":
                summary = params.get('summary')
                start_str = params.get('start')
                end_str = params.get('end')

                if not all([summary, start_str, end_str]):
                    return "Error: summary, start, and end are required for creating an event"

                try:
                    start = datetime.fromisoformat(start_str)
                    end = datetime.fromisoformat(end_str)
                except ValueError as e:
                    return f"Error: Invalid datetime format: {e}"

                description = params.get('description')
                location = params.get('location')
                attendees = params.get('attendees')
                timezone = params.get('timezone', 'UTC')

                event = client.create_event(
                    summary=summary,
                    start=start,
                    end=end,
                    description=description,
                    location=location,
                    attendees=attendees,
                    calendar_id=calendar_id,
                    timezone=timezone
                )

                return f"✓ Created event: {summary} on {start_str}"

            elif action == "update":
                event_id = params.get('event_id')
                if not event_id:
                    return "Error: event_id is required for updating an event"

                # Build updates dict from provided parameters
                updates = {}
                if 'summary' in params:
                    updates['summary'] = params['summary']
                if 'start' in params:
                    try:
                        start = datetime.fromisoformat(params['start'])
                        timezone = params.get('timezone', 'UTC')
                        updates['start'] = {
                            'dateTime': start.isoformat(),
                            'timeZone': timezone
                        }
                    except ValueError as e:
                        return f"Error: Invalid start datetime format: {e}"
                if 'end' in params:
                    try:
                        end = datetime.fromisoformat(params['end'])
                        timezone = params.get('timezone', 'UTC')
                        updates['end'] = {
                            'dateTime': end.isoformat(),
                            'timeZone': timezone
                        }
                    except ValueError as e:
                        return f"Error: Invalid end datetime format: {e}"
                if 'description' in params:
                    updates['description'] = params['description']
                if 'location' in params:
                    updates['location'] = params['location']

                if not updates:
                    return "Error: No fields to update provided"

                event = client.update_event(event_id, calendar_id=calendar_id, **updates)
                return f"✓ Updated event: {event.get('summary', event_id)}"

            elif action == "delete":
                event_id = params.get('event_id')
                if not event_id:
                    return "Error: event_id is required for deleting an event"

                client.delete_event(event_id, calendar_id=calendar_id)
                return f"✓ Deleted event: {event_id}"

            elif action == "search":
                query = params.get('query')
                if not query:
                    return "Error: query is required for searching"

                events = client.search_events(query, calendar_id=calendar_id)
                if not events:
                    return f"No events found matching '{query}'."

                # Include event IDs in search results so they can be used for update/delete
                summaries = [client.format_event_summary(e, include_id=True) for e in events]
                return f"Events matching '{query}' ({len(events)}):\n" + "\n".join(f"• {s}" for s in summaries)

            elif action == "quick_add":
                text = params.get('query') or params.get('summary')
                if not text:
                    return "Error: query or summary is required for quick_add"

                event = client.quick_add_event(text, calendar_id=calendar_id)
                return f"✓ Created event: {event.get('summary', text)}"

            else:
                return f"Error: Unknown action: {action}"

        except Exception as e:
            self.logger.error(f"Google Calendar error: {e}")
            return f"Error: {str(e)}"
