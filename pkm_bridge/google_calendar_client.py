"""Google Calendar API client for event management."""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Get logger
logger = logging.getLogger(__name__)

# The API caps a page at 2500 events and only ever sorts ascending, so the
# newest events sit on the last page: reading a range to its end is the only
# way to drop the oldest events rather than the newest when a query overflows
# max_results. MAX_PAGES bounds a runaway query (an unbounded search over many
# years) at a cost of one request per 2500 events.
PAGE_SIZE = 2500
MAX_PAGES = 20


@dataclass
class EventList:
    """Events from a calendar query, with the total that matched it.

    `total` counts everything in range, so a caller can tell a complete answer
    from one trimmed to `max_results` instead of reading a short list as the
    whole story.
    """

    events: List[Dict[str, Any]] = field(default_factory=list)
    total: int = 0

    @property
    def truncated(self) -> bool:
        """True if older events were dropped to fit max_results."""
        return self.total > len(self.events)


def _rfc3339_utc(dt: datetime) -> str:
    """Format a datetime as an RFC3339 UTC timestamp for the API.

    Naive datetimes are assumed to already be UTC (the form produced internally
    by get_today_events/get_week_events); aware ones are converted to true UTC
    instead of having their wall-clock time reinterpreted as UTC.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
    return dt.isoformat() + "Z"


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
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        )

        # Build Calendar API service
        self.service = build("calendar", "v3", credentials=self.credentials)

    def list_calendars(self) -> List[Dict[str, Any]]:
        """Get all calendars.

        Returns:
            List of calendar dictionaries with id, summary, etc.

        Raises:
            Exception: If fetching calendars fails
        """
        try:
            calendar_list = self.service.calendarList().list().execute()
            return calendar_list.get("items", [])
        except HttpError as e:
            raise Exception(f"Failed to list calendars: {e}")

    def _query_events(
        self, params: Dict[str, Any], max_results: int, newest_first: bool
    ) -> EventList:
        """Page through events.list, keeping the newest `max_results` matches.

        Args:
            params: Parameters for events().list(), minus paging.
            max_results: Most events to keep. The API returns them oldest-first,
                so the excess is trimmed off the front and the newest survive.
            newest_first: Reverse the result into descending order.

        Returns:
            EventList of the kept events and the total the query matched.
        """
        kept: List[Dict[str, Any]] = []
        total = 0
        page_token = None

        for _ in range(MAX_PAGES):
            page = (
                self.service.events()
                .list(**params, maxResults=PAGE_SIZE, pageToken=page_token)
                .execute()
            )
            items = page.get("items", [])
            total += len(items)
            kept.extend(items)
            if len(kept) > max_results:
                del kept[: len(kept) - max_results]

            page_token = page.get("nextPageToken")
            if not page_token:
                break
        else:
            logger.warning(
                f"Calendar query stopped at the {MAX_PAGES}-page limit after {total} events; "
                "narrow the time range for a complete count"
            )

        if newest_first:
            kept.reverse()

        return EventList(kept, total)

    def _event_params(
        self,
        calendar_id: str,
        time_min: Optional[datetime],
        time_max: Optional[datetime],
        single_events: bool = True,
        order_by: str = "startTime",
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build the events().list() parameters shared by listing and search."""
        params: Dict[str, Any] = {
            "calendarId": calendar_id,
            "singleEvents": single_events,
            "orderBy": order_by,
        }
        if time_min:
            params["timeMin"] = _rfc3339_utc(time_min)
        if time_max:
            params["timeMax"] = _rfc3339_utc(time_max)
        if query:
            params["q"] = query
        return params

    def get_events(
        self,
        calendar_id: str = "primary",
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: int = 50,
        single_events: bool = True,
        order_by: str = "startTime",
        newest_first: bool = True,
    ) -> EventList:
        """Get events from a calendar.

        Args:
            calendar_id: Calendar ID (default: 'primary')
            time_min: Start of time range (default: now)
            time_max: End of time range
            max_results: Most events to return. When the range holds more, the
                OLDEST are dropped, so a lookback always reaches what happened
                most recently.
            single_events: Expand recurring events into instances
            order_by: Sort order ('startTime' or 'updated')
            newest_first: Return newest first (default). Pass False for agenda
                order.

        Returns:
            EventList of events and the total matching the range.

        Raises:
            Exception: If fetching events fails
        """
        try:
            # Default to now if no time_min provided
            if time_min is None:
                time_min = datetime.now(ZoneInfo("UTC"))

            params = self._event_params(calendar_id, time_min, time_max, single_events, order_by)
            return self._query_events(params, max_results, newest_first)

        except HttpError as e:
            raise Exception(f"Failed to list events: {e}")

    def get_today_events(
        self, calendar_id: str = "primary", user_timezone: str = None
    ) -> EventList:
        """Get events for today, in chronological order.

        Args:
            calendar_id: Calendar ID (default: 'primary')
            user_timezone: User's timezone string (e.g., 'America/New_York').
                If None, uses server local time.

        Returns:
            EventList of today's events
        """
        # Use user's timezone if provided, otherwise server local time
        if user_timezone:
            try:
                tz = ZoneInfo(user_timezone)
                now = datetime.now(tz)
                logger.info(f"Getting today's events. Current time in {user_timezone}: {now}")
            except Exception as e:
                logger.warning(
                    f"Invalid timezone '{user_timezone}', falling back to server local: {e}"
                )
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
        time_min_utc = time_min.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        time_max_utc = time_max.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

        logger.info(f"UTC time range for API: {time_min_utc} to {time_max_utc}")

        result = self.get_events(
            calendar_id=calendar_id,
            time_min=time_min_utc,
            time_max=time_max_utc,
            newest_first=False,
        )

        logger.info(f"Found {result.total} events for today")
        for event in result.events:
            start = event.get("start", {})
            logger.debug(
                f"  Event: {event.get('summary')} at {start.get('dateTime') or start.get('date')}"
            )

        return result

    def get_week_events(self, calendar_id: str = "primary", user_timezone: str = None) -> EventList:
        """Get events for the next 7 days, in chronological order.

        Args:
            calendar_id: Calendar ID (default: 'primary')
            user_timezone: User's timezone string (e.g., 'America/New_York').
                If None, uses server local time.

        Returns:
            EventList of this week's events
        """
        # Use user's timezone if provided, otherwise server local time
        if user_timezone:
            try:
                tz = ZoneInfo(user_timezone)
                now = datetime.now(tz)
                logger.info(f"Getting week's events. Current time in {user_timezone}: {now}")
            except Exception as e:
                logger.warning(
                    f"Invalid timezone '{user_timezone}', falling back to server local: {e}"
                )
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
        time_min_utc = time_min.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        time_max_utc = time_max.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

        logger.info(f"UTC time range for API: {time_min_utc} to {time_max_utc}")

        result = self.get_events(
            calendar_id=calendar_id,
            time_min=time_min_utc,
            time_max=time_max_utc,
            newest_first=False,
        )

        logger.info(f"Found {result.total} events for the week")

        return result

    def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        calendar_id: str = "primary",
        timezone: str = "UTC",
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
                "summary": summary,
                "start": {
                    "dateTime": start.isoformat(),
                    "timeZone": timezone,
                },
                "end": {
                    "dateTime": end.isoformat(),
                    "timeZone": timezone,
                },
            }

            if description:
                event["description"] = description

            if location:
                event["location"] = location

            if attendees:
                event["attendees"] = [{"email": email} for email in attendees]

            created_event = (
                self.service.events().insert(calendarId=calendar_id, body=event).execute()
            )

            return created_event

        except HttpError as e:
            raise Exception(f"Failed to create event '{summary}': {e}")

    def update_event(
        self, event_id: str, calendar_id: str = "primary", **updates
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
            event = self.service.events().get(calendarId=calendar_id, eventId=event_id).execute()

            # Apply updates
            for key, value in updates.items():
                event[key] = value

            # Update the event
            updated_event = (
                self.service.events()
                .update(calendarId=calendar_id, eventId=event_id, body=event)
                .execute()
            )

            return updated_event

        except HttpError as e:
            raise Exception(f"Failed to update event {event_id}: {e}")

    def delete_event(self, event_id: str, calendar_id: str = "primary") -> None:
        """Delete an event.

        Args:
            event_id: Event ID to delete
            calendar_id: Calendar ID (default: 'primary')

        Raises:
            Exception: If deletion fails
        """
        try:
            self.service.events().delete(calendarId=calendar_id, eventId=event_id).execute()

        except HttpError as e:
            raise Exception(f"Failed to delete event {event_id}: {e}")

    def search_events(
        self,
        query: str,
        calendar_id: str = "primary",
        max_results: int = 50,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        newest_first: bool = True,
    ) -> EventList:
        """Search for events by text query.

        Searches the whole calendar unless bounded by time_min/time_max.

        Args:
            query: Search query string
            calendar_id: Calendar ID (default: 'primary')
            max_results: Most matches to return; the oldest are dropped first.
            time_min: Only match events starting at or after this time
            time_max: Only match events starting before this time
            newest_first: Return newest first (default)

        Returns:
            EventList of matching events and the total that matched.

        Raises:
            Exception: If the search fails
        """
        try:
            params = self._event_params(calendar_id, time_min, time_max, query=query)
            return self._query_events(params, max_results, newest_first)

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
        summary = event.get("summary", "Untitled Event")
        event_id = event.get("id", "")

        # Get start time
        start = event.get("start", {})
        start_str = ""

        if "dateTime" in start:
            # Timed event
            start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
            start_str = start_dt.strftime("%Y-%m-%d %H:%M")
        elif "date" in start:
            # All-day event
            start_str = f"{start['date']} (all day)"

        # Get location if present
        location = event.get("location", "")
        location_str = f" @ {location}" if location else ""

        # Include ID if requested (useful for updates/deletes)
        id_str = f" [ID: {event_id}]" if include_id and event_id else ""

        return f"{summary} - {start_str}{location_str}{id_str}"

    def quick_add_event(self, text: str, calendar_id: str = "primary") -> Dict[str, Any]:
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
            created_event = (
                self.service.events().quickAdd(calendarId=calendar_id, text=text).execute()
            )

            return created_event

        except HttpError as e:
            raise Exception(f"Failed to quick add event '{text}': {e}")
