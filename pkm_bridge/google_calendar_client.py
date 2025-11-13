"""Google Calendar API client for event management."""

import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Get logger
logger = logging.getLogger(__name__)


class GoogleCalendarClient:
    """Client for Google Calendar API."""

    def __init__(self, access_token: str, refresh_token: Optional[str] = None):
        """Initialize Google Calendar client.

        Args:
            access_token: OAuth access token
            refresh_token: Optional OAuth refresh token
        """
        # Create credentials object
        self.credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv('GOOGLE_CLIENT_ID'),
            client_secret=os.getenv('GOOGLE_CLIENT_SECRET')
        )

        # Build Calendar API service
        self.service = build('calendar', 'v3', credentials=self.credentials)

    def list_calendars(self) -> List[Dict[str, Any]]:
        """Get all calendars.

        Returns:
            List of calendar dictionaries with id, summary, etc.

        Raises:
            Exception: If fetching calendars fails
        """
        try:
            calendar_list = self.service.calendarList().list().execute()
            return calendar_list.get('items', [])
        except HttpError as e:
            raise Exception(f"Failed to list calendars: {e}")

    def get_events(
        self,
        calendar_id: str = 'primary',
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: int = 50,
        single_events: bool = True,
        order_by: str = 'startTime'
    ) -> List[Dict[str, Any]]:
        """Get events from a calendar.

        Args:
            calendar_id: Calendar ID (default: 'primary')
            time_min: Start of time range (default: now)
            time_max: End of time range
            max_results: Maximum number of events to return
            single_events: Expand recurring events into instances
            order_by: Sort order ('startTime' or 'updated')

        Returns:
            List of event dictionaries

        Raises:
            Exception: If fetching events fails
        """
        try:
            # Default to now if no time_min provided
            if time_min is None:
                time_min = datetime.utcnow()

            # Format times as RFC3339 timestamps
            params = {
                'calendarId': calendar_id,
                'timeMin': time_min.isoformat() + 'Z',
                'maxResults': max_results,
                'singleEvents': single_events,
                'orderBy': order_by
            }

            if time_max:
                params['timeMax'] = time_max.isoformat() + 'Z'

            events_result = self.service.events().list(**params).execute()
            return events_result.get('items', [])

        except HttpError as e:
            raise Exception(f"Failed to list events: {e}")

    def get_today_events(self, calendar_id: str = 'primary', user_timezone: str = None) -> List[Dict[str, Any]]:
        """Get events for today.

        Args:
            calendar_id: Calendar ID (default: 'primary')
            user_timezone: User's timezone string (e.g., 'America/New_York'). If None, uses server local time.

        Returns:
            List of today's events
        """
        # Use user's timezone if provided, otherwise server local time
        if user_timezone:
            try:
                tz = ZoneInfo(user_timezone)
                now = datetime.now(tz)
                logger.info(f"Getting today's events. Current time in {user_timezone}: {now}")
            except Exception as e:
                logger.warning(f"Invalid timezone '{user_timezone}', falling back to server local: {e}")
                now = datetime.now()
                logger.info(f"Getting today's events. Current server local time: {now}")
        else:
            now = datetime.now()
            logger.info(f"Getting today's events. Current server local time: {now}")

        # Get timezone-aware start/end of day
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
        time_max = time_min + timedelta(days=1)

        logger.info(f"Local time range: {time_min} to {time_max}")

        # Convert to UTC for API query (remove tzinfo after conversion)
        time_min_utc = time_min.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
        time_max_utc = time_max.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)

        logger.info(f"UTC time range for API: {time_min_utc} to {time_max_utc}")

        events = self.get_events(
            calendar_id=calendar_id,
            time_min=time_min_utc,
            time_max=time_max_utc
        )

        logger.info(f"Found {len(events)} events for today")
        for event in events:
            start = event.get('start', {})
            logger.debug(f"  Event: {event.get('summary')} at {start.get('dateTime') or start.get('date')}")

        return events

    def get_week_events(self, calendar_id: str = 'primary', user_timezone: str = None) -> List[Dict[str, Any]]:
        """Get events for the next 7 days.

        Args:
            calendar_id: Calendar ID (default: 'primary')
            user_timezone: User's timezone string (e.g., 'America/New_York'). If None, uses server local time.

        Returns:
            List of this week's events
        """
        # Use user's timezone if provided, otherwise server local time
        if user_timezone:
            try:
                tz = ZoneInfo(user_timezone)
                now = datetime.now(tz)
                logger.info(f"Getting week's events. Current time in {user_timezone}: {now}")
            except Exception as e:
                logger.warning(f"Invalid timezone '{user_timezone}', falling back to server local: {e}")
                now = datetime.now()
                logger.info(f"Getting week's events. Current server local time: {now}")
        else:
            now = datetime.now()
            logger.info(f"Getting week's events. Current server local time: {now}")

        # Get timezone-aware start of today + 7 days
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
        time_max = time_min + timedelta(days=7)

        logger.info(f"Local time range: {time_min} to {time_max}")

        # Convert to UTC for API query (remove tzinfo after conversion)
        time_min_utc = time_min.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
        time_max_utc = time_max.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)

        logger.info(f"UTC time range for API: {time_min_utc} to {time_max_utc}")

        events = self.get_events(
            calendar_id=calendar_id,
            time_min=time_min_utc,
            time_max=time_max_utc
        )

        logger.info(f"Found {len(events)} events for the week")

        return events

    def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        calendar_id: str = 'primary',
        timezone: str = 'UTC'
    ) -> Dict[str, Any]:
        """Create a new calendar event.

        Args:
            summary: Event title
            start: Event start time
            end: Event end time
            description: Event description
            location: Event location
            attendees: List of attendee email addresses
            calendar_id: Calendar ID (default: 'primary')
            timezone: Timezone for the event (default: 'UTC')

        Returns:
            Created event dictionary

        Raises:
            Exception: If event creation fails
        """
        try:
            event = {
                'summary': summary,
                'start': {
                    'dateTime': start.isoformat(),
                    'timeZone': timezone,
                },
                'end': {
                    'dateTime': end.isoformat(),
                    'timeZone': timezone,
                }
            }

            if description:
                event['description'] = description

            if location:
                event['location'] = location

            if attendees:
                event['attendees'] = [{'email': email} for email in attendees]

            created_event = self.service.events().insert(
                calendarId=calendar_id,
                body=event
            ).execute()

            return created_event

        except HttpError as e:
            raise Exception(f"Failed to create event '{summary}': {e}")

    def update_event(
        self,
        event_id: str,
        calendar_id: str = 'primary',
        **updates
    ) -> Dict[str, Any]:
        """Update an existing event.

        Args:
            event_id: Event ID to update
            calendar_id: Calendar ID (default: 'primary')
            **updates: Fields to update (summary, start, end, description, location, etc.)

        Returns:
            Updated event dictionary

        Raises:
            Exception: If update fails
        """
        try:
            # First, get the current event
            event = self.service.events().get(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()

            # Apply updates
            for key, value in updates.items():
                event[key] = value

            # Update the event
            updated_event = self.service.events().update(
                calendarId=calendar_id,
                eventId=event_id,
                body=event
            ).execute()

            return updated_event

        except HttpError as e:
            raise Exception(f"Failed to update event {event_id}: {e}")

    def delete_event(
        self,
        event_id: str,
        calendar_id: str = 'primary'
    ) -> None:
        """Delete an event.

        Args:
            event_id: Event ID to delete
            calendar_id: Calendar ID (default: 'primary')

        Raises:
            Exception: If deletion fails
        """
        try:
            self.service.events().delete(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()

        except HttpError as e:
            raise Exception(f"Failed to delete event {event_id}: {e}")

    def search_events(
        self,
        query: str,
        calendar_id: str = 'primary',
        max_results: int = 50
    ) -> List[Dict[str, Any]]:
        """Search for events by text query.

        Args:
            query: Search query string
            calendar_id: Calendar ID (default: 'primary')
            max_results: Maximum number of results

        Returns:
            List of matching events
        """
        try:
            events_result = self.service.events().list(
                calendarId=calendar_id,
                q=query,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            return events_result.get('items', [])

        except HttpError as e:
            raise Exception(f"Failed to search events for '{query}': {e}")

    def format_event_summary(self, event: Dict[str, Any], include_id: bool = False) -> str:
        """Format an event into a human-readable summary.

        Args:
            event: Event dictionary
            include_id: Whether to include event ID in output (default: False)

        Returns:
            Formatted event summary string
        """
        summary = event.get('summary', 'Untitled Event')
        event_id = event.get('id', '')

        # Get start time
        start = event.get('start', {})
        start_str = ''

        if 'dateTime' in start:
            # Timed event
            start_dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
            start_str = start_dt.strftime('%Y-%m-%d %H:%M')
        elif 'date' in start:
            # All-day event
            start_str = f"{start['date']} (all day)"

        # Get location if present
        location = event.get('location', '')
        location_str = f" @ {location}" if location else ''

        # Include ID if requested (useful for updates/deletes)
        id_str = f" [ID: {event_id}]" if include_id and event_id else ''

        return f"{summary} - {start_str}{location_str}{id_str}"

    def quick_add_event(
        self,
        text: str,
        calendar_id: str = 'primary'
    ) -> Dict[str, Any]:
        """Create an event from a natural language text string.

        Uses Google's Quick Add feature to parse text like:
        "Appointment at Somewhere on June 3rd 10am-10:25am"
        "Dinner with John tomorrow 7pm"

        Args:
            text: Natural language event description
            calendar_id: Calendar ID (default: 'primary')

        Returns:
            Created event dictionary

        Raises:
            Exception: If quick add fails
        """
        try:
            created_event = self.service.events().quickAdd(
                calendarId=calendar_id,
                text=text
            ).execute()

            return created_event

        except HttpError as e:
            raise Exception(f"Failed to quick add event '{text}': {e}")
