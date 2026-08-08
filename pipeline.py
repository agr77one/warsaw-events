from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import html
import io
import json
import os
import re
import smtplib
import sqlite3
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo

import dateparser
import httpx
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
DOCS = ROOT / "docs"
for folder in (DATA, OUTPUT, DOCS):
    folder.mkdir(exist_ok=True)

DB_PATH = DATA / "events.db"
USER_AGENT = "WarsawEventsPipeline/1.0 (+https://github.com/agr77one/warsaw-events)"
WARSAW_TIMEZONE = ZoneInfo("America/Indiana/Indianapolis")
COMMUNITY_FORM_URL = (
    "https://docs.google.com/forms/d/e/"
    "1FAIpQLSf3XuV_y1QgqL9byWZYKt0Q_TrEGBKU1k0b4Pv7_qF7Au7Rfg/viewform"
)
COMMUNITY_FEED_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1Yh1bXAiwe_ArXnINUhSSZyWbDWXu9Dz_W4Lg0t_HyYI/"
    "gviz/tq?tqx=out:csv&sheet=Approved%20Events"
)
COMMUNITY_SOURCE_NAME = "Approved community submissions"
INCLUDE_TERMS = (
    "fair", "festival", "carnival", "parade", "fireworks", "boat", "flotilla",
    "water ski", "waterski", "car show", "cruise-in", "rodeo", "tractor pull",
    "demolition derby", "art fair", "craft fair", "concert", "theater", "musical",
    "museum", "exhibit", "food festival", "market", "5k", "10k", "triathlon",
    "cycling", "bike ride", "community celebration", "heritage days",
)
EXCLUDE_TERMS = (
    "closed board meeting", "private meeting", "trustees meeting",
)
IMPORTANT_TERMS = (
    "fair", "festival", "carnival", "parade", "fireworks", "car show", "cruise-in",
    "rodeo", "tractor pull", "demolition derby", "water ski", "waterski",
)

PROXIMITY_BANDS = (
    (10, 4),
    (25, 3),
    (50, 2),
    (75, 1),
)

CITY_DISTANCE_MILES = {
    "warsaw": 0,
    "winona lake": 3,
    "atwood": 9,
    "claypool": 10,
    "pierceton": 10,
    "leesburg": 11,
    "etna green": 13,
    "mentone": 14,
    "milford": 14,
    "larwill": 15,
    "north webster": 16,
    "silver lake": 16,
    "syracuse": 18,
    "north manchester": 18,
    "columbia city": 21,
    "rochester": 24,
    "nappanee": 25,
    "plymouth": 30,
    "goshen": 30,
    "wabash": 33,
    "huntington": 40,
    "elkhart": 41,
    "shipshewana": 44,
    "fort wayne": 45,
    "south bend": 47,
    "mishawaka": 48,
}

CHANGE_FIELDS = (
    "title", "start", "end", "venue", "address", "city", "state",
    "description", "admission", "event_url", "status",
)


@dataclass
class Event:
    title: str
    start: str
    end: str | None
    venue: str | None
    address: str | None
    city: str | None
    state: str | None
    description: str
    admission: str | None
    source_name: str
    source_url: str
    event_url: str
    confidence: str
    category: str = "Community"
    image_url: str | None = None
    distance_miles: float | None = None
    status: str = "CONFIRMED"
    importance: int = 0
    fingerprint: str = ""


def load_sources() -> list[dict]:
    with (ROOT / "config" / "sources.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f).get("sources", [])


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return dateparser.parse(
        value,
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
            "TIMEZONE": "America/Indiana/Indianapolis",
        },
    )


def parse_event_dates(value: str | None) -> tuple[datetime | None, datetime | None]:
    """Parse common calendar date ranges while preserving the event's start time."""
    if not value:
        return None, None
    normalized = clean(value.replace("@", " at ").replace("–", "-")) or ""
    normalized = normalized.replace("–", "-")
    normalized = re.sub(r"^(?:date|time):\s*", "", normalized, flags=re.I)
    calendar_range = re.fullmatch(
        r"([A-Za-z]+)\s+(\d{1,2})\s*-\s*(?:([A-Za-z]+)\s+)?(\d{1,2}),\s*(\d{4})",
        normalized,
    )
    if calendar_range:
        start_month, start_day, end_month, end_day, year = calendar_range.groups()
        start = parse_date(f"{start_month} {start_day}, {year}")
        end = parse_date(f"{end_month or start_month} {end_day}, {year}")
        return start, end
    parts = re.split(r"\s+-\s+", normalized, maxsplit=1)
    start_text = re.sub(r"\s+from\s+", " at ", parts[0], flags=re.I)
    start = parse_date(start_text)
    if not start or len(parts) == 1:
        return start, None
    end_text = parts[1]
    if re.fullmatch(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)", end_text, re.I):
        end = parse_date(f"{start:%B %d, %Y} at {end_text}")
    else:
        end = parse_date(end_text)
    return start, end


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", value).strip() or None


def is_public_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def extract_approved_submissions(csv_text: str) -> list[Event]:
    """Map the sanitized, approved Google Sheet export into normal events."""
    required = (
        "Submission ID", "Event title", "Start date", "Start time", "Venue name",
        "Street address", "City", "State", "Event description",
        "Admission or price", "Official event or ticket URL",
    )
    events: list[Event] = []
    for raw_row in csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"))):
        row = {key: clean(value) for key, value in raw_row.items() if key}
        if any(not row.get(field) for field in required):
            continue
        event_url = row["Official event or ticket URL"]
        if not is_public_url(event_url):
            continue
        start = parse_date(f"{row['Start date']} {row['Start time']}")
        if not start:
            continue
        end_date = row.get("End date") or row["Start date"]
        end = parse_date(f"{end_date} {row['End time']}") if row.get("End time") else None
        image_url = row.get("Image URL") if is_public_url(row.get("Image URL")) else None
        submission_id = row["Submission ID"]
        source_url = f"{COMMUNITY_FORM_URL}#submission={quote(submission_id)}"
        title = row["Event title"]
        description = row["Event description"]
        events.append(Event(
            title=title,
            start=start.isoformat(),
            end=end.isoformat() if end else None,
            venue=row["Venue name"],
            address=row["Street address"],
            city=row["City"],
            state=row["State"],
            description=description,
            admission=row["Admission or price"],
            source_name=COMMUNITY_SOURCE_NAME,
            source_url=source_url,
            event_url=event_url,
            confidence="B",
            category=categorize(title, description),
            image_url=image_url,
        ))
    return events


def readable_text(value: str | None) -> str | None:
    if value is None:
        return None
    decoded = html.unescape(value).replace("\\n", " ").replace("\\r", " ").replace("\\t", " ")
    text = BeautifulSoup(decoded, "html.parser").get_text(" ", strip=True)
    return clean(text)


def confidence_for(source: dict) -> str:
    reliability = source.get("reliability")
    if reliability in {"official", "tourism"}:
        return "A"
    if reliability == "aggregator":
        return "C"
    return "B"


def categorize(title: str, description: str = "") -> str:
    text = f"{title} {description}".casefold()
    groups = (
        ("Music & shows", ("concert", "music", "musical", "theatre", "theater", "comedy", "film", "movie", "dance", "opera", "jazz")),
        ("Family", ("family", "kids", "kid's", "children", "child", "toddler", "story time", "storytime", "lego", "teen")),
        ("Food & markets", ("market", "food", "wine", "beer", "taste", "dinner", "farmers", "culinary", "coffee")),
        ("Sports & outdoors", ("run", "5k", "10k", "race", "bike", "cycling", "sport", "baseball", "football", "hike", "outdoor", "ski", "boat", "fitness")),
        ("Arts & learning", ("art", "craft", "museum", "exhibit", "class", "workshop", "book", "library", "history", "lecture", "learn")),
        ("Festivals", ("festival", "fair", "parade", "fireworks", "carnival", "celebration", "days")),
    )
    for category, terms in groups:
        if any(term in text for term in terms):
            return category
    return "Community"


def fetch_text(client: httpx.Client, url: str) -> str:
    """Fetch a public page, falling back to curl for sites that reject httpx TLS."""
    try:
        response = client.get(url)
        response.raise_for_status()
        text = response.text
        if "�" in text:
            windows_text = response.content.decode("cp1252", errors="replace")
            if windows_text.count("�") < text.count("�"):
                text = windows_text
        return text
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in {403, 406, 429}:
            raise
    command = ["curl", "--location", "--fail", "--silent", "--show-error", "--max-time", "35", url]
    result = subprocess.run(command, check=True, capture_output=True, timeout=40)
    text = result.stdout.decode("utf-8", errors="replace")
    if "�" in text:
        windows_text = result.stdout.decode("cp1252", errors="replace")
        if windows_text.count("�") < text.count("�"):
            text = windows_text
    return text


class SourceNotConfigured(RuntimeError):
    """A supported source is intentionally dormant until credentials are supplied."""


def infer_admission(description: str | None) -> str | None:
    text = readable_text(description) or ""
    if re.search(r"included (?:with|in) (?:gate|fair) admission", text, re.I):
        return "Included with gate admission"
    if re.search(r"\bfree\b|no admission|no charge", text, re.I):
        return "Free"
    price = re.search(r"(?:\$|USD\s*)(\d+(?:\.\d{2})?)", text, re.I)
    return f"$ {price.group(1)}" if price else None


def parse_ics_datetime(value: str) -> datetime | None:
    raw = value.strip()
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            parsed = datetime.strptime(raw, pattern)
            if pattern.endswith("Z"):
                parsed = parsed.replace(tzinfo=timezone.utc).astimezone(WARSAW_TIMEZONE).replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    return None


def extract_ics(calendar_text: str, source: dict) -> list[Event]:
    """Extract public iCalendar feeds, including explicitly listed recurrence dates."""
    unfolded = re.sub(r"\n[ \t]", "", calendar_text.replace("\r\n", "\n").replace("\r", "\n"))
    events: list[Event] = []
    for block in re.findall(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", unfolded, re.S):
        values: dict[str, list[str]] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            values.setdefault(key.split(";", 1)[0].upper(), []).append(value)
        starts = list(values.get("DTSTART", []))
        if not starts:
            continue
        starts += [item for entry in values.get("RDATE", []) for item in entry.split(",")]
        excluded = {item for entry in values.get("EXDATE", []) for item in entry.split(",")}
        base_start = parse_ics_datetime(starts[0])
        base_end = parse_ics_datetime((values.get("DTEND") or [""])[0])
        duration = base_end - base_start if base_start and base_end else None
        title = readable_text((values.get("SUMMARY") or ["Untitled event"])[0]) or "Untitled event"
        description = readable_text((values.get("DESCRIPTION") or [""])[0].replace("\\n", "\n")) or ""
        venue = readable_text((values.get("LOCATION") or [source.get("venue") or ""])[0]) or source.get("venue")
        event_url = (values.get("URL") or [source["url"]])[0].replace("\\,", ",")
        image_url = (values.get("ATTACH") or [None])[0]
        for raw_start in starts:
            if raw_start in excluded:
                continue
            start = parse_ics_datetime(raw_start)
            if not start:
                continue
            end = start + duration if duration else None
            events.append(Event(
                title=title, start=start.isoformat(), end=end.isoformat() if end else None,
                venue=venue, address=source.get("address"), city=source.get("city"),
                state=source.get("state", "IN"), description=description,
                admission=infer_admission(description), source_name=source["name"],
                source_url=source["url"], event_url=event_url,
                confidence=confidence_for(source), category=categorize(title, description),
                image_url=image_url, distance_miles=source.get("distance_miles"),
            ))
    return events


def extract_facebook_graph(client: httpx.Client, source: dict) -> list[Event]:
    """Use Meta's supported Graph API; never scrape Facebook HTML."""
    token = os.getenv(source.get("token_env", "FACEBOOK_PAGE_ACCESS_TOKEN"))
    page_id = source.get("page_id") or os.getenv(source.get("page_id_env", "FACEBOOK_PAGE_ID"))
    api_version = os.getenv(source.get("api_version_env", "FACEBOOK_GRAPH_API_VERSION"))
    if not token or not page_id or not api_version:
        raise SourceNotConfigured("Meta API credentials, Page ID, or API version are not configured")
    endpoint = f"https://graph.facebook.com/{api_version.strip('/')}/{page_id}/events"
    fields = "id,name,description,start_time,end_time,place,cover,ticket_uri,event_times,is_canceled"
    events: list[Event] = []
    after = None
    for _ in range(3):
        params = {"fields": fields, "limit": 100}
        if after:
            params["after"] = after
        response = client.get(endpoint, params=params, headers={"Authorization": f"Bearer {token}"})
        if response.is_error:
            raise RuntimeError(f"Meta Graph API returned HTTP {response.status_code}")
        payload = response.json()
        for item in payload.get("data", []):
            occurrences = item.get("event_times") or [item]
            for occurrence in occurrences:
                start = parse_date(occurrence.get("start_time") or item.get("start_time"))
                if not start:
                    continue
                end = parse_date(occurrence.get("end_time") or item.get("end_time"))
                place = item.get("place") if isinstance(item.get("place"), dict) else {}
                location = place.get("location") if isinstance(place.get("location"), dict) else {}
                description = readable_text(item.get("description")) or ""
                event_id = str(occurrence.get("id") or item.get("id") or "")
                cover = item.get("cover") if isinstance(item.get("cover"), dict) else {}
                events.append(Event(
                    title=readable_text(item.get("name")) or "Untitled event",
                    start=start.isoformat(), end=end.isoformat() if end else None,
                    venue=readable_text(place.get("name")) or source.get("venue"),
                    address=readable_text(location.get("street")) or source.get("address"),
                    city=readable_text(location.get("city")) or source.get("city"),
                    state=readable_text(location.get("state")) or source.get("state", "IN"),
                    description=description, admission=infer_admission(description),
                    source_name=source["name"], source_url=source["url"],
                    event_url=f"https://www.facebook.com/events/{event_id}/" if event_id else source["url"],
                    confidence=confidence_for(source), category=categorize(item.get("name", ""), description),
                    image_url=cover.get("source"), distance_miles=source.get("distance_miles"),
                    status="CANCELED" if item.get("is_canceled") else "CONFIRMED",
                ))
        cursors = payload.get("paging", {}).get("cursors", {})
        after = cursors.get("after") if payload.get("paging", {}).get("next") else None
        if not after:
            break
    return events


def iter_jsonld(payload):
    if isinstance(payload, list):
        for item in payload:
            yield from iter_jsonld(item)
    elif isinstance(payload, dict):
        if "@graph" in payload:
            yield from iter_jsonld(payload["@graph"])
        else:
            yield payload


def extract_jsonld(html_text: str, source: dict) -> list[Event]:
    soup = BeautifulSoup(html_text, "html.parser")
    events: list[Event] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or script.get_text())
        except Exception:
            continue
        for item in iter_jsonld(payload):
            types = item.get("@type", []) if isinstance(item, dict) else []
            if isinstance(types, str):
                types = [types]
            if "Event" not in types:
                continue
            start_dt = parse_date(item.get("startDate"))
            if not start_dt:
                continue
            location = item.get("location") or {}
            if isinstance(location, list):
                location = location[0] if location else {}
            address = location.get("address") or {} if isinstance(location, dict) else {}
            if isinstance(address, str):
                street, city, state = address, None, "IN"
            else:
                street = address.get("streetAddress")
                city = address.get("addressLocality")
                state = address.get("addressRegion") or "IN"
            offers = item.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            admission = None
            if isinstance(offers, dict) and offers.get("price") is not None:
                price = offers.get("price")
                admission = "Free" if str(price).strip() in {"0", "0.0", "0.00"} else f"{offers.get('priceCurrency', '$')} {price}"
            desc = readable_text(str(item.get("description", ""))) or ""
            title = readable_text(str(item.get("name", ""))) or "Untitled event"
            image_value = item.get("image")
            if isinstance(image_value, list):
                image_value = image_value[0] if image_value else None
            if isinstance(image_value, dict):
                image_value = image_value.get("url") or image_value.get("contentUrl")
            end_dt = parse_date(item.get("endDate"))
            events.append(Event(
                title=title,
                start=start_dt.isoformat(),
                end=end_dt.isoformat() if end_dt else None,
                venue=readable_text(location.get("name")) if isinstance(location, dict) else source.get("venue"),
                address=readable_text(street) or source.get("address"),
                city=readable_text(city) or source.get("city"),
                state=readable_text(state) or source.get("state", "IN"),
                description=desc,
                admission=clean(admission), source_name=source["name"],
                source_url=source["url"], event_url=item.get("url") or source["url"],
                confidence=confidence_for(source), category=categorize(title, desc),
                image_url=clean(str(image_value)) if image_value else None,
                distance_miles=source.get("distance_miles"),
            ))
    return events


def extract_generic(html_text: str, source: dict) -> list[Event]:
    soup = BeautifulSoup(html_text, "html.parser")
    events: list[Event] = []
    selectors = source.get("selectors", {})
    blocks = selectors.get("event_block", "article, .event, .tribe-events-calendar-list__event-row, .event-item")
    for block in soup.select(blocks):
        title_el = block.select_one(selectors.get("title", "h1, h2, h3, .event-title, .title"))
        date_el = block.select_one(selectors.get("date", "time, .date, .event-date, .tribe-event-date-start"))
        if not title_el or not date_el:
            continue
        start_dt, end_dt = parse_event_dates(date_el.get("datetime") or date_el.get_text(" ", strip=True))
        if not start_dt:
            continue
        a = title_el.find("a") if title_el.name != "a" else title_el
        if not a and block.name == "a":
            a = block
        href = urljoin(source["url"], a.get("href")) if a and a.get("href") else source["url"]
        venue_el = block.select_one(selectors.get("venue", ".venue, .location, .event-location"))
        description_el = block.select_one(selectors.get("description", ".description, .summary, .event-description"))
        image_el = block.select_one(selectors.get("image", "img"))
        title = readable_text(title_el.get_text(" ", strip=True)) or "Untitled event"
        description = readable_text(description_el.get_text(" ", strip=True)) if description_el else ""
        events.append(Event(
            title=title,
            start=start_dt.isoformat(), end=end_dt.isoformat() if end_dt else None,
            venue=readable_text(venue_el.get_text(" ", strip=True)) if venue_el else source.get("venue"),
            address=source.get("address"), city=source.get("city"), state=source.get("state", "IN"),
            description=description or readable_text(block.get_text(" ", strip=True)) or "",
            admission=None, source_name=source["name"], source_url=source["url"], event_url=href,
            confidence=confidence_for(source), category=categorize(title, description or ""),
            image_url=urljoin(source["url"], image_el.get("src")) if image_el and image_el.get("src") else None,
            distance_miles=source.get("distance_miles"),
        ))
    return events


def extract_paginated_generic(
    client: httpx.Client, first_page: str, source: dict
) -> list[Event]:
    """Walk a bounded, configured set of ordinary `?page=N` calendar pages."""
    events = extract_generic(first_page, source)
    seen_urls = {event.event_url for event in events}
    for page in range(1, int(source.get("pagination_pages", 1))):
        separator = "&" if "?" in source["url"] else "?"
        page_url = f"{source['url']}{separator}page={page}"
        page_source = {**source, "url": page_url}
        page_events = extract_generic(fetch_text(client, page_url), page_source)
        new_events = [event for event in page_events if event.event_url not in seen_urls]
        if not new_events:
            break
        for event in new_events:
            event.source_url = source["url"]
        events.extend(new_events)
        seen_urls.update(event.event_url for event in new_events)
    return events


def extract_librarycalendar_feed(html_text: str, source: dict) -> list[Event]:
    """Read LibraryCalendar's historical daily feed, used for coverage audits."""
    soup = BeautifulSoup(html_text, "html.parser")
    events: list[Event] = []
    for block in soup.select("article.event-card"):
        link = block.select_one(".lc-event__title .lc-event__link")
        if not link:
            continue
        aria = link.get("aria-label", "")
        date_match = re.search(r"\bon\s+(.+?)\s+@\s+(.+)$", aria, re.I)
        if not date_match:
            continue
        start = parse_date(f"{date_match.group(1)} at {date_match.group(2)}")
        if not start:
            continue
        time_el = block.select_one(".lc-event-info-item--time")
        time_text = time_el.get_text(" ", strip=True) if time_el else ""
        times = re.findall(r"\d{1,2}:\d{2}\s*(?:am|pm)", time_text, re.I)
        end = parse_date(f"{start:%B %d, %Y} at {times[-1]}") if len(times) > 1 else None
        if end and end <= start:
            end += timedelta(days=1)
        room = block.select_one(".lc-event__room")
        description_el = block.select_one(".lc-event__body")
        category_el = block.select_one(".lc-event-info__item--categories, .lc-event__program-types span")
        image_el = block.select_one("img")
        title = readable_text(link.get_text(" ", strip=True)) or "Untitled event"
        description = readable_text(description_el.get_text(" ", strip=True)) if description_el else ""
        block_text = block.get_text(" ", strip=True).casefold()
        status = "CANCELED" if "cancel" in block_text else (
            "SOLD_OUT" if "closed" in block_text else "CONFIRMED"
        )
        events.append(Event(
            title=title, start=start.isoformat(), end=end.isoformat() if end else None,
            venue=readable_text(room.get_text(" ", strip=True).replace("Room:", "")) if room else source.get("venue"),
            address=source.get("address"), city=source.get("city"), state=source.get("state", "IN"),
            description=description or "", admission=infer_admission(description),
            source_name=source["name"], source_url=source["url"],
            event_url=urljoin(source["url"], link.get("href", "")),
            confidence=confidence_for(source),
            category=readable_text(category_el.get_text(" ", strip=True)) if category_el else categorize(title, description or ""),
            image_url=urljoin(source["url"], image_el.get("src")) if image_el and image_el.get("src") else None,
            distance_miles=source.get("distance_miles"),
            status=status,
        ))
    return events


def extract_allevents(html_text: str, source: dict) -> list[Event]:
    """Read the public event payload rendered into AllEvents city pages."""
    matches = list(re.finditer(r"_this\.events_data\s*=\s*(\[.*?\]);", html_text, re.S))
    if not matches:
        return extract_jsonld(html_text, source)
    payload_text = max(matches, key=lambda match: len(match.group(1))).group(1)
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return extract_jsonld(html_text, source)
    events: list[Event] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        start_dt = parse_date(item.get("start_time_display"))
        if not start_dt:
            continue
        end_dt = parse_date(item.get("end_time_display"))
        if end_dt == start_dt:
            end_dt = None
        title = readable_text(item.get("eventname_raw") or item.get("eventname")) or "Untitled event"
        # City-index descriptions are often auto-generated promotional copy.
        # Title, exact time, venue, price, and link are the dependable fields.
        description = ""
        venue_data = item.get("venue") if isinstance(item.get("venue"), dict) else {}
        tickets = item.get("tickets") if isinstance(item.get("tickets"), dict) else {}
        admission = readable_text(item.get("display_price_label"))
        if not admission and tickets.get("min_ticket_price") is not None:
            price = tickets.get("min_ticket_price")
            admission = "Free" if str(price) in {"0", "0.0", "0.00"} else f"{tickets.get('ticket_currency', '$')} {price}"
        events.append(Event(
            title=title, start=start_dt.isoformat(), end=end_dt.isoformat() if end_dt else None,
            venue=readable_text(item.get("location")) or source.get("venue"),
            address=readable_text(venue_data.get("street")) or source.get("address"),
            city=readable_text(venue_data.get("city")) or source.get("city"),
            state=readable_text(venue_data.get("state")) or source.get("state", "IN"),
            description=description, admission=admission, source_name=source["name"],
            source_url=source["url"], event_url=item.get("event_url") or source["url"],
            confidence=confidence_for(source), category=categorize(title, description),
            image_url=item.get("banner_url") or item.get("thumb_url_large") or item.get("thumb_url"),
            distance_miles=source.get("distance_miles"),
        ))
    return events or extract_jsonld(html_text, source)


def extract_detail_page(html_text: str, source: dict, event_url: str) -> list[Event]:
    selectors = source.get("detail_selectors", {})
    soup = BeautifulSoup(html_text, "html.parser")
    title_el = soup.select_one(selectors.get("title", "h1"))
    date_el = soup.select_one(selectors.get("date", "time"))
    if not title_el or not date_el:
        return []
    date_text = date_el.get("datetime") or date_el.get_text(" ", strip=True)
    time_el = soup.select_one(selectors["time"]) if selectors.get("time") else None
    start_dt, end_dt = parse_event_dates(date_text)
    if time_el and start_dt:
        time_text = re.sub(r"^time:\s*", "", time_el.get_text(" ", strip=True), flags=re.I)
        timed_start, timed_end = parse_event_dates(f"{start_dt:%B %d, %Y} at {time_text}")
        if timed_start:
            start_dt = timed_start
        if end_dt and timed_end:
            end_dt = end_dt.replace(hour=timed_end.hour, minute=timed_end.minute)
        elif timed_end:
            end_dt = timed_end
    if not start_dt:
        return []
    description_el = soup.select_one(selectors.get("description", ".event-description, .entry-content p"))
    venue_el = soup.select_one(selectors["venue"]) if selectors.get("venue") else None
    admission_el = soup.select_one(selectors["admission"]) if selectors.get("admission") else None
    image_el = soup.select_one(selectors.get("image", "main img, article img"))
    title = readable_text(title_el.get_text(" ", strip=True)) or "Untitled event"
    description = readable_text(description_el.get_text(" ", strip=True)) if description_el else ""
    admission = readable_text(admission_el.get_text(" ", strip=True)) if admission_el else infer_admission(description)
    return [Event(
        title=title, start=start_dt.isoformat(), end=end_dt.isoformat() if end_dt else None,
        venue=readable_text(venue_el.get_text(" ", strip=True)) if venue_el else source.get("venue"),
        address=source.get("address"), city=source.get("city"),
        state=source.get("state", "IN"), description=description or "", admission=admission,
        source_name=source["name"], source_url=source["url"], event_url=event_url,
        confidence=confidence_for(source), category=categorize(title, description or ""),
        image_url=urljoin(event_url, image_el.get("src")) if image_el and image_el.get("src") else None,
        distance_miles=source.get("distance_miles"),
    )]


def extract_detail_admission(html_text: str) -> str | None:
    page_text = readable_text(BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True)) or ""
    price_match = re.search(
        r"Ticket Prices:\s*(.+?)(?:Pricing includes|Content Warnings|Venue|$)",
        page_text,
        re.I,
    )
    if price_match:
        return clean(price_match.group(1))
    if re.search(r"included (?:with|in) (?:gate|fair) admission", page_text, re.I):
        return "Included with gate admission"
    return None


def extract_linked(client: httpx.Client, html_text: str, source: dict) -> list[Event]:
    soup = BeautifulSoup(html_text, "html.parser")
    selector = source.get("link_selector", "a[href]")
    links: list[str] = []
    for anchor in soup.select(selector):
        href = urljoin(source["url"], anchor.get("href", ""))
        if href.rstrip("/") == source["url"].rstrip("/") or href in links:
            continue
        links.append(href)
    events: list[Event] = []
    for event_url in links[:30]:
        try:
            detail_html = fetch_text(client, event_url)
            extracted = extract_jsonld(detail_html, source)
            if not extracted:
                extracted = extract_detail_page(detail_html, source, event_url)
            published_admission = extract_detail_admission(detail_html)
            for event in extracted:
                event.event_url = event_url
                event.admission = event.admission or published_admission
            events.extend(extracted)
        except Exception:
            continue
    return events


def proximity_bonus(distance_miles: float | None) -> int:
    if distance_miles is None:
        return 0
    for maximum_distance, bonus in PROXIMITY_BANDS:
        if distance_miles <= maximum_distance:
            return bonus
    return 0


def filter_and_score(event: Event, now: datetime, highlights_only: bool = False) -> Event | None:
    text = f"{event.title} {event.description}".lower()
    if any(term in text for term in EXCLUDE_TERMS):
        return None
    if highlights_only and not any(term in text for term in INCLUDE_TERMS):
        return None
    if re.search(r"\bcancel(?:ed|led|lation)?\b", text):
        event.status = "CANCELED"
    elif re.search(r"\bsold out\b", text):
        event.status = "SOLD_OUT"
    start = datetime.fromisoformat(event.start)
    if start < now - timedelta(days=1) or start > now + timedelta(days=180):
        return None
    city = (event.city or "").casefold().strip()
    event.distance_miles = CITY_DISTANCE_MILES.get(city, event.distance_miles)
    event.category = event.category or categorize(event.title, event.description)
    event.importance = {"A": 6, "B": 3, "C": 1}.get(event.confidence, 1)
    event.importance += 3 if any(term in text for term in IMPORTANT_TERMS) else 0
    event.importance += 1 if event.venue else 0
    event.importance += 1 if event.city or event.address else 0
    event.importance += proximity_bonus(event.distance_miles) * 3
    normalized_title = re.sub(r"[^a-z0-9]+", " ", event.title.casefold()).strip()
    key = "|".join([normalized_title, start.strftime("%Y-%m-%d"), (event.city or "").casefold()])
    event.fingerprint = hashlib.sha256(key.encode()).hexdigest()
    return event


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS events (
      fingerprint TEXT PRIMARY KEY, title TEXT NOT NULL, start TEXT NOT NULL, end TEXT,
      venue TEXT, address TEXT, city TEXT, state TEXT, description TEXT, admission TEXT,
      source_name TEXT, source_url TEXT, event_url TEXT, confidence TEXT, status TEXT,
      importance INTEGER, first_seen TEXT, last_seen TEXT, payload_json TEXT
    );
    CREATE TABLE IF NOT EXISTS changes (
      id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT, detected_at TEXT,
      change_type TEXT, before_json TEXT, after_json TEXT
    );
    CREATE TABLE IF NOT EXISTS source_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, source_name TEXT, source_url TEXT,
      checked_at TEXT, status TEXT, event_count INTEGER, error TEXT
    );
    """)
    conn.commit()


def upsert_event(conn: sqlite3.Connection, event: Event, now_iso: str) -> str:
    payload = asdict(event)
    row = conn.execute("SELECT payload_json FROM events WHERE fingerprint=?", (event.fingerprint,)).fetchone()
    if row is None:
        conn.execute("""INSERT INTO events (
            fingerprint, title, start, end, venue, address, city, state, description,
            admission, source_name, source_url, event_url, confidence, status,
            importance, first_seen, last_seen, payload_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            event.fingerprint, event.title, event.start, event.end, event.venue, event.address,
            event.city, event.state, event.description, event.admission, event.source_name,
            event.source_url, event.event_url, event.confidence, event.status, event.importance,
            now_iso, now_iso, json.dumps(payload),
        ))
        conn.execute("INSERT INTO changes(fingerprint,detected_at,change_type,after_json) VALUES(?,?,?,?)",
                     (event.fingerprint, now_iso, "NEW", json.dumps(payload)))
        return "NEW"
    before = json.loads(row[0])
    change = "UNCHANGED"
    before_content = {field: before.get(field) for field in CHANGE_FIELDS}
    after_content = {field: payload.get(field) for field in CHANGE_FIELDS}
    if before_content != after_content:
        change = event.status if event.status in {"CANCELED", "SOLD_OUT"} else "UPDATED"
        conn.execute("INSERT INTO changes(fingerprint,detected_at,change_type,before_json,after_json) VALUES(?,?,?,?,?)",
                     (event.fingerprint, now_iso, change, json.dumps(before), json.dumps(payload)))
    conn.execute("""UPDATE events SET title=?,start=?,end=?,venue=?,address=?,city=?,state=?,description=?,
                 admission=?,source_name=?,source_url=?,event_url=?,confidence=?,status=?,importance=?,
                 last_seen=?,payload_json=? WHERE fingerprint=?""", (
        event.title,event.start,event.end,event.venue,event.address,event.city,event.state,
        event.description,event.admission,event.source_name,event.source_url,event.event_url,
        event.confidence,event.status,event.importance,now_iso,json.dumps(payload),event.fingerprint,
    ))
    return change


def dedupe(events: list[Event]) -> list[Event]:
    seen: dict[str, Event] = {}
    for event in events:
        current = seen.get(event.fingerprint)
        if current is None or event.confidence < current.confidence:
            seen[event.fingerprint] = event
    return sorted(seen.values(), key=lambda e: (e.start, e.title))


def reconcile_community_submissions(
    conn: sqlite3.Connection,
    active_events: list[Event],
    now_iso: str,
) -> int:
    """Withdraw community rows absent from a successfully fetched approved feed."""
    active_source_urls = {event.source_url for event in active_events}
    rows = conn.execute(
        "SELECT fingerprint,source_url,payload_json FROM events WHERE source_name=?",
        (COMMUNITY_SOURCE_NAME,),
    ).fetchall()
    removed = 0
    for fingerprint, source_url, payload_json in rows:
        if source_url in active_source_urls:
            continue
        before = json.loads(payload_json)
        after = {**before, "status": "WITHDRAWN"}
        conn.execute(
            "INSERT INTO changes(fingerprint,detected_at,change_type,before_json,after_json) "
            "VALUES(?,?,?,?,?)",
            (fingerprint, now_iso, "WITHDRAWN", payload_json, json.dumps(after)),
        )
        conn.execute("DELETE FROM events WHERE fingerprint=?", (fingerprint,))
        removed += 1
    return removed


def crawl(conn: sqlite3.Connection, now: datetime) -> tuple[list[Event], list[dict]]:
    client = httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    found: list[Event] = []
    health: list[dict] = []
    for source in load_sources():
        try:
            if source.get("extractor") == "facebook_graph":
                extracted = extract_facebook_graph(client, source)
            else:
                html_text = fetch_text(client, source["url"])
            if source.get("extractor") == "facebook_graph":
                pass
            elif source.get("extractor") == "ics":
                extracted = extract_ics(html_text, source)
            elif source.get("pagination_pages"):
                extracted = extract_paginated_generic(client, html_text, source)
            elif "allevents.in/" in source["url"]:
                extracted = extract_allevents(html_text, source)
            elif source.get("extractor") == "linked":
                extracted = extract_linked(client, html_text, source)
            else:
                extracted = extract_jsonld(html_text, source)
                if not extracted:
                    extracted = extract_generic(html_text, source)
            accepted = [e for e in (
                filter_and_score(x, now, source.get("highlights_only", False)) for x in extracted
            ) if e]
            found.extend(accepted)
            status = "ok" if accepted else ("no_upcoming" if extracted else "empty")
            error = None
        except SourceNotConfigured as exc:
            extracted, accepted, status, error = [], [], "not_configured", str(exc)
        except Exception as exc:
            extracted, accepted, status, error = [], [], "failed", str(exc)
        health.append({"name": source["name"], "url": source["url"], "status": status,
                       "raw_event_count": len(extracted), "event_count": len(accepted), "error": error})
        conn.execute("INSERT INTO source_runs(source_name,source_url,checked_at,status,event_count,error) VALUES(?,?,?,?,?,?)",
                     (source["name"], source["url"], now.isoformat(), status, len(accepted), error))
    community_feed_url = os.getenv("COMMUNITY_EVENTS_FEED_URL", COMMUNITY_FEED_URL).strip()
    if community_feed_url:
        try:
            feed_text = fetch_text(client, community_feed_url)
            extracted = extract_approved_submissions(feed_text)
            accepted = [event for event in (
                filter_and_score(item, now) for item in extracted
            ) if event]
            found.extend(accepted)
            removed = reconcile_community_submissions(conn, accepted, now.isoformat())
            status = "ok" if accepted else ("no_upcoming" if extracted else "empty")
            error = f"{removed} withdrawn" if removed else None
        except Exception as exc:
            extracted, accepted, status, error = [], [], "failed", str(exc)
        health.append({
            "name": COMMUNITY_SOURCE_NAME,
            "url": COMMUNITY_FORM_URL,
            "status": status,
            "raw_event_count": len(extracted),
            "event_count": len(accepted),
            "error": error,
        })
        conn.execute(
            "INSERT INTO source_runs(source_name,source_url,checked_at,status,event_count,error) "
            "VALUES(?,?,?,?,?,?)",
            (COMMUNITY_SOURCE_NAME, COMMUNITY_FORM_URL, now.isoformat(), status, len(accepted), error),
        )
    conn.commit()
    client.close()
    return dedupe(found), health


def query_events(conn: sqlite3.Connection, now: datetime, days: int = 120) -> list[dict]:
    rows = conn.execute("SELECT payload_json FROM events WHERE start>=? AND start<? ORDER BY start,title",
                        (now.isoformat(), (now + timedelta(days=days)).isoformat())).fetchall()
    events = [json.loads(row[0]) for row in rows]
    source_defaults = {source["name"]: source for source in load_sources()}
    for event in events:
        for field in ("title", "venue", "address", "city", "description", "admission"):
            event[field] = readable_text(event.get(field))
        defaults = source_defaults.get(event.get("source_name"), {})
        event["city"] = event.get("city") or defaults.get("city")
        event["state"] = event.get("state") or defaults.get("state")
        if event.get("distance_miles") is None:
            event["distance_miles"] = defaults.get("distance_miles")
        event.setdefault("category", categorize(event.get("title") or "", event.get("description") or ""))
        event.setdefault("image_url", None)
    events = dedupe_event_dicts(events)
    return sorted(events, key=lambda event: (
        event.get("distance_miles") if event.get("distance_miles") is not None else 999,
        event["start"], -event.get("importance", 0), event["title"],
    ))


def normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def titles_are_similar(left: str, right: str) -> bool:
    left_norm, right_norm = normalized_title(left), normalized_title(right)
    if left_norm == right_norm:
        return True
    shorter, longer = sorted((left_norm, right_norm), key=len)
    if len(shorter) >= 7 and (longer.startswith(shorter + " ") or longer.endswith(" " + shorter)):
        return True
    return difflib.SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.88


def dedupe_event_dicts(events: list[dict]) -> list[dict]:
    """Collapse same-day title variants while preferring the more reliable source."""
    confidence_rank = {"A": 0, "B": 1, "C": 2}
    ordered = sorted(events, key=lambda event: (
        confidence_rank.get(event.get("confidence"), 3), event["start"], event["title"],
    ))
    kept: list[dict] = []
    for event in ordered:
        event_date = datetime.fromisoformat(event["start"]).date()
        city = (event.get("city") or "").casefold().strip()
        duplicate = any(
            datetime.fromisoformat(existing["start"]).date() == event_date
            and (existing.get("city") or "").casefold().strip() == city
            and titles_are_similar(existing["title"], event["title"])
            for existing in kept
        )
        if not duplicate:
            kept.append(event)
    return kept


def recent_changes(conn: sqlite3.Connection, now: datetime, hours: int) -> list[dict]:
    rows = conn.execute("SELECT fingerprint,change_type,after_json FROM changes WHERE detected_at>=? ORDER BY detected_at DESC",
                        ((now - timedelta(hours=hours)).isoformat(),)).fetchall()
    result = []
    seen: set[tuple[str, str]] = set()
    for fingerprint, change_type, payload in rows:
        identity = (fingerprint, change_type)
        if identity in seen:
            continue
        seen.add(identity)
        item = json.loads(payload) if payload else {}
        item["change_type"] = change_type
        result.append(item)
    return result


def export_csv(events: list[dict]) -> None:
    fields = ["title","start","end","venue","address","city","state","description","admission",
              "source_name","source_url","event_url","confidence","category","image_url","distance_miles","status",
              "importance","fingerprint"]
    with (OUTPUT / "events.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(events)


def event_zone(event: dict) -> tuple[str, str, int]:
    distance = event.get("distance_miles")
    if distance is not None and distance <= 10:
        return "local", "Warsaw & Winona Lake", 0
    if distance is not None and distance <= 25:
        return "nearby", "Nearby in Kosciusko County", 1
    return "regional", "Worth the drive", 2


def event_time(event: dict) -> str:
    dt = datetime.fromisoformat(event["start"])
    if dt.hour == 0 and dt.minute == 0:
        return "Time to be confirmed"
    end = datetime.fromisoformat(event["end"]) if event.get("end") else None
    value = dt.strftime("%I:%M %p").lstrip("0")
    if end and end.date() == dt.date():
        value += f"–{end.strftime('%I:%M %p').lstrip('0')}"
    return value


def event_location(event: dict, include_address: bool = False) -> str:
    fields = [event.get("venue")]
    if include_address:
        fields.append(event.get("address"))
    fields.extend([event.get("city"), event.get("state")])
    values: list[str] = []
    for value in fields:
        if not value:
            continue
        contained = any(re.search(rf"\b{re.escape(value)}\b", existing, re.I) for existing in values)
        if value not in values and not contained:
            values.append(value)
    return ", ".join(values) or "Location to be confirmed"


def render_newsletter(events: list[dict], changes: list[dict], now: datetime) -> tuple[str, str]:
    all_upcoming = [event for event in events if datetime.fromisoformat(event["start"]) <= now + timedelta(days=14)]
    close_to_home = [event for event in all_upcoming if event_zone(event)[0] != "regional"]
    regional = [event for event in all_upcoming if event_zone(event)[0] == "regional"]
    regional = sorted(regional, key=lambda event: (
        -event.get("importance", 0), event["start"], event["title"],
    ))[:18]
    upcoming = close_to_home + regional
    upcoming.sort(key=lambda event: (
        event_zone(event)[2], event["start"], -event.get("importance", 0), event["title"],
    ))
    local_count = sum(event_zone(event)[0] == "local" for event in all_upcoming)
    md = [
        "# Warsaw Weekend", "", f"Your guide for {now:%B %d}–{now + timedelta(days=14):%B %d, %Y}", "",
        f"{len(all_upcoming)} events found in the next two weeks, including {local_count} in Warsaw and Winona Lake.",
        "This email includes every close-to-home event plus 18 regional highlights. Browse the dashboard for the complete calendar.", "",
    ]
    sections: list[str] = []
    for zone_key, zone_title, _ in (("local", "Closest to home", 0), ("nearby", "Around Kosciusko County", 1), ("regional", "Worth the drive", 2)):
        zone_events = [event for event in upcoming if event_zone(event)[0] == zone_key]
        if not zone_events:
            continue
        md.extend([f"# {zone_title}", ""])
        rows: list[str] = []
        for event in zone_events:
            dt = datetime.fromisoformat(event["start"])
            location = event_location(event)
            description = (event.get("description") or "").strip()
            if len(description) > 220:
                description = description[:217].rsplit(" ", 1)[0] + "…"
            distance = event.get("distance_miles")
            distance_text = "Warsaw area" if distance is not None and distance <= 10 else (
                f"About {distance:g} miles away" if distance is not None else "Distance pending"
            )
            md.extend([
                f"## {dt:%A, %B %d} — {event['title']}",
                f"**{event_time(event)} · {location}**", description,
                f"{event.get('category', 'Community')} · {distance_text} · [Details]({event.get('event_url') or event.get('source_url')})", "",
            ])
            rows.append(
                "<tr><td style='padding:0 0 14px'><table role='presentation' width='100%' style='border-collapse:collapse;background:#fff;border:1px solid #e7e2d9;border-radius:14px'>"
                f"<tr><td style='width:76px;padding:16px;text-align:center;background:#f5efe4;border-radius:14px 0 0 14px;vertical-align:top'><div style='font-size:12px;font-weight:800;letter-spacing:.08em;color:#a4492e;text-transform:uppercase'>{dt:%b}</div><div style='font-size:28px;font-weight:800;color:#1f2933'>{dt:%d}</div></td>"
                f"<td style='padding:15px 18px'><div style='font-size:12px;font-weight:700;color:#a4492e;text-transform:uppercase;letter-spacing:.06em'>{html.escape(event.get('category', 'Community'))} · {html.escape(distance_text)}</div>"
                f"<h3 style='font-size:18px;line-height:1.25;margin:5px 0;color:#16212e'>{html.escape(event['title'])}</h3>"
                f"<div style='font-size:14px;line-height:1.5;color:#4b5563'><strong>{html.escape(event_time(event))}</strong><br>{html.escape(location)}</div>"
                f"{f'<p style=\"font-size:14px;line-height:1.5;color:#4b5563;margin:8px 0\">{html.escape(description)}</p>' if description else ''}"
                f"<a href='{html.escape(event.get('event_url') or event.get('source_url'), quote=True)}' style='font-size:14px;font-weight:700;color:#a4492e'>Event details →</a></td></tr></table></td></tr>"
            )
        sections.append(
            f"<tr><td style='padding:24px 24px 8px'><h2 style='margin:0;font-size:22px;color:#16212e'>{html.escape(zone_title)}</h2>"
            f"<p style='margin:5px 0 0;color:#6b7280;font-size:14px'>{len(zone_events)} events</p></td></tr>"
            f"<tr><td style='padding:8px 24px'><table role='presentation' width='100%' style='border-collapse:collapse'>{''.join(rows)}</table></td></tr>"
        )
    if changes:
        md += ["# Newly found or updated", ""] + [f"- **{x['change_type']}** {x.get('title')}" for x in changes[:20]]
    portal_url = "https://agr77one.github.io/warsaw-events/"
    email_html = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Warsaw Weekend</title></head><body style='margin:0;background:#f4f1eb;font-family:Arial,sans-serif;color:#16212e'>
<table role='presentation' width='100%' style='border-collapse:collapse;background:#f4f1eb'><tr><td align='center' style='padding:24px 10px'>
<table role='presentation' width='100%' style='max-width:680px;border-collapse:collapse;background:#fbfaf7;border-radius:20px;overflow:hidden'>
<tr><td style='padding:34px 28px;background:#173b3f;color:#fff'><div style='font-size:12px;font-weight:800;letter-spacing:.16em;text-transform:uppercase;color:#f6bd60'>Warsaw area events</div><h1 style='font-size:34px;line-height:1.05;margin:9px 0'>Warsaw Weekend</h1><p style='font-size:16px;line-height:1.5;margin:0;color:#d8e7e5'>{len(all_upcoming)} events found over the next two weeks · {local_count} close to home</p><p style='font-size:13px;line-height:1.45;margin:10px 0 0;color:#bcd2d0'>Every local event, plus selected regional highlights. The dashboard has the complete calendar.</p></td></tr>
{''.join(sections)}
<tr><td align='center' style='padding:12px 24px 34px'><a href='{portal_url}' style='display:inline-block;background:#a4492e;color:#fff;text-decoration:none;font-weight:700;padding:13px 20px;border-radius:999px'>Browse every event</a><p style='font-size:12px;color:#7b8490;margin:18px 0 0'>Updated {now:%B %d, %Y} · Recipients are sent privately by Bcc.</p></td></tr>
</table></td></tr></table></body></html>"""
    return "\n".join(md), email_html


def render_portal(events: list[dict], health: list[dict], now: datetime) -> str:
    categories = sorted({event.get("category", "Community") for event in events})
    grouped_cards: dict[str, list[str]] = {"local": [], "nearby": [], "regional": []}
    ranked_events = sorted(events, key=lambda event: (
        event_zone(event)[2], event["start"], -event.get("importance", 0), event["title"],
    ))
    for event in ranked_events:
        dt = datetime.fromisoformat(event["start"])
        location = event_location(event)
        distance = event.get("distance_miles")
        zone, _, _ = event_zone(event)
        proximity = "In the Warsaw area" if zone == "local" else (
            f"About {distance:g} miles from Warsaw" if distance is not None else "Regional event"
        )
        description = (event.get("description") or "").strip()
        if len(description) > 190:
            description = description[:187].rsplit(" ", 1)[0] + "…"
        image_url = event.get("image_url")
        image = (
            f"<img class='event-image' src='{html.escape(image_url, quote=True)}' alt='' loading='lazy' referrerpolicy='no-referrer'>"
            if image_url else ""
        )
        category = event.get("category", "Community")
        days_away = max(0, (dt.date() - now.date()).days)
        search_text = " ".join(str(x or "") for x in (
            event.get("title"), event.get("description"), event.get("venue"), event.get("city"), category,
        )).casefold()
        source_label = "Reviewed community submission" if event.get("source_name") == COMMUNITY_SOURCE_NAME else (
            "Official calendar" if event.get("confidence") == "A" else (
                "Local reporting" if event.get("confidence") == "B" else "Community listing"
            )
        )
        grouped_cards[zone].append(
            f"<article class='event-card' data-event data-zone='{zone}' data-days='{days_away}' data-category='{html.escape(category, quote=True)}' data-search='{html.escape(search_text, quote=True)}'>"
            f"{image}<div class='event-body'><div class='event-top'><div class='date-tile'><span>{dt:%b}</span><strong>{dt:%d}</strong><small>{dt:%a}</small></div>"
            f"<div class='event-heading'><div class='pill-row'><span class='pill category'>{html.escape(category)}</span><span class='pill distance'>{html.escape(proximity)}</span></div>"
            f"<h3>{html.escape(event['title'])}</h3><p class='when'>{html.escape(event_time(event))}</p></div></div>"
            f"<p class='where'><span aria-hidden='true'>●</span> {html.escape(location)}</p>"
            f"{f'<p class=\"description\">{html.escape(description)}</p>' if description else ''}"
            f"<div class='card-footer'><span>{html.escape(source_label)} · {html.escape(event.get('source_name', 'Source'))}</span>"
            f"<a href='{html.escape(event.get('event_url') or event.get('source_url'), quote=True)}'>View details <span aria-hidden='true'>↗</span></a></div></div></article>"
        )
    configured_health = [x for x in health if x["status"] != "not_configured"]
    checked = sum(x["status"] != "failed" for x in configured_health)
    contributing = sum(x.get("event_count", 0) > 0 for x in configured_health)
    pending_sources = len(health) - len(configured_health)
    local_count = len(grouped_cards["local"])
    nearby_count = len(grouped_cards["nearby"])
    week_count = sum(datetime.fromisoformat(event["start"]) <= now + timedelta(days=7) for event in events)
    options = "".join(f"<option value='{html.escape(category, quote=True)}'>{html.escape(category)}</option>" for category in categories)
    sections = "".join(
        f"<section class='event-section' data-section='{key}'><div class='section-heading'><div><span class='eyebrow'>{eyebrow}</span><h2>{title}</h2></div><span class='section-count'>{len(grouped_cards[key])} events</span></div><div class='event-grid'>{''.join(grouped_cards[key])}</div></section>"
        for key, title, eyebrow in (
            ("local", "Warsaw & Winona Lake", "Closest to home"),
            ("nearby", "Around Kosciusko County", "A short drive"),
            ("regional", "Worth the drive", "Regional calendar"),
        ) if grouped_cards[key]
    )
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='description' content='A Warsaw-first calendar of events in Kosciusko County and nearby northern Indiana communities.'><title>Warsaw Weekend · Local Events</title>
<style>
:root{{--ink:#15232d;--muted:#66727c;--paper:#f4f1ea;--card:#fffdf9;--line:#ddd8ce;--green:#173b3f;--green2:#245b5d;--orange:#b65031;--gold:#f1b85b;--shadow:0 14px 38px rgba(30,45,48,.09)}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}a{{color:inherit}}.hero{{position:relative;overflow:hidden;background:linear-gradient(125deg,#102f33 0%,#1c4b4d 58%,#a4492e 140%);color:#fff}}.hero:after{{content:"";position:absolute;width:420px;height:420px;border:90px solid rgba(255,255,255,.055);border-radius:50%;right:-170px;top:-210px}}.hero-inner{{position:relative;z-index:1;max-width:1180px;margin:auto;padding:66px 28px 100px}}.brand{{display:flex;align-items:center;gap:10px;font-size:13px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:#ffe0a7}}.brand-mark{{display:grid;place-items:center;width:34px;height:34px;background:var(--gold);color:#163538;border-radius:10px;font-size:18px}}h1{{max-width:720px;font-family:Georgia,serif;font-size:clamp(44px,7vw,78px);line-height:.98;letter-spacing:-.04em;margin:26px 0 18px}}.hero-copy{{max-width:650px;font-size:18px;line-height:1.65;color:#d9e9e7;margin:0}}.updated{{margin-top:24px;font-size:13px;color:#b9d0ce}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;max-width:1180px;margin:-48px auto 0;padding:0 28px;position:relative;z-index:2}}.stat{{background:var(--card);border:1px solid rgba(0,0,0,.05);border-radius:18px;padding:20px;box-shadow:var(--shadow)}}.stat strong{{display:block;font-family:Georgia,serif;font-size:34px;color:var(--green)}}.stat span{{font-size:13px;color:var(--muted)}}main{{max-width:1180px;margin:auto;padding:34px 28px 80px}}.filters{{position:sticky;top:12px;z-index:10;background:rgba(255,253,249,.94);backdrop-filter:blur(16px);border:1px solid var(--line);border-radius:18px;padding:14px;box-shadow:0 10px 30px rgba(30,45,48,.08);display:grid;grid-template-columns:minmax(220px,2fr) repeat(3,minmax(140px,1fr));gap:10px}}.control{{position:relative}}.control label{{position:absolute;left:14px;top:8px;font-size:10px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);pointer-events:none}}input,select{{width:100%;height:58px;border:1px solid #d8d3c8;border-radius:12px;background:#fff;color:var(--ink);font:inherit;padding:23px 14px 7px;outline:none}}input:focus,select:focus{{border-color:var(--green2);box-shadow:0 0 0 3px rgba(36,91,93,.13)}}.results-bar{{display:flex;justify-content:space-between;align-items:center;margin:28px 2px 10px;color:var(--muted);font-size:14px}}#clear{{border:0;background:none;color:var(--orange);font-weight:800;cursor:pointer}}.event-section{{padding-top:34px}}.section-heading{{display:flex;align-items:end;justify-content:space-between;border-bottom:1px solid #d6d0c5;padding-bottom:14px;margin-bottom:18px}}.eyebrow{{display:block;font-size:11px;font-weight:900;letter-spacing:.13em;text-transform:uppercase;color:var(--orange);margin-bottom:5px}}.section-heading h2{{font-family:Georgia,serif;font-size:clamp(28px,4vw,40px);margin:0;letter-spacing:-.02em}}.section-count{{font-size:13px;color:var(--muted)}}.event-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}.event-card{{overflow:hidden;background:var(--card);border:1px solid var(--line);border-radius:18px;box-shadow:0 5px 18px rgba(31,45,46,.05);transition:transform .18s ease,box-shadow .18s ease}}.event-card:hover{{transform:translateY(-2px);box-shadow:var(--shadow)}}.event-image{{width:100%;height:190px;object-fit:cover;background:#dfe8e6}}.event-body{{padding:20px}}.event-top{{display:flex;gap:16px;align-items:flex-start}}.date-tile{{flex:0 0 62px;text-align:center;border:1px solid #e1d9ca;border-radius:14px;overflow:hidden;background:#faf5eb}}.date-tile span{{display:block;padding:5px;background:var(--orange);color:#fff;font-size:11px;font-weight:900;letter-spacing:.1em;text-transform:uppercase}}.date-tile strong{{display:block;font-family:Georgia,serif;font-size:26px;line-height:1;padding-top:8px}}.date-tile small{{display:block;padding:3px 0 8px;color:var(--muted);font-size:11px;text-transform:uppercase}}.event-heading{{min-width:0}}.pill-row{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:7px}}.pill{{display:inline-block;border-radius:999px;padding:4px 8px;font-size:10px;font-weight:800;letter-spacing:.03em}}.category{{background:#e5efed;color:#235557}}.distance{{background:#faeadf;color:#934128}}.event-card h3{{font-family:Georgia,serif;font-size:23px;line-height:1.13;margin:0;letter-spacing:-.015em}}.when{{font-weight:800;color:var(--orange);margin:7px 0 0;font-size:14px}}.where{{font-size:14px;line-height:1.45;color:#4f5c65;margin:16px 0 8px}}.where span{{color:var(--gold);font-size:10px}}.description{{font-size:14px;line-height:1.55;color:var(--muted);margin:10px 0}}.card-footer{{display:flex;justify-content:space-between;gap:14px;align-items:end;border-top:1px solid #ece7df;margin-top:16px;padding-top:13px;font-size:11px;color:#7b858c}}.card-footer a{{flex:0 0 auto;color:var(--orange);font-size:13px;font-weight:850;text-decoration:none}}.empty{{display:none;text-align:center;padding:70px 20px}}.empty h2{{font-family:Georgia,serif;font-size:30px;margin:0 0 8px}}footer{{background:#102f33;color:#c9dcda;padding:28px;text-align:center;font-size:12px}}footer a{{color:#ffe0a7}}[hidden]{{display:none!important}}
@media(max-width:800px){{.hero-inner{{padding:46px 20px 78px}}.stats{{grid-template-columns:repeat(2,1fr);padding:0 16px;margin-top:-38px}}main{{padding:26px 16px 60px}}.filters{{position:relative;top:0;grid-template-columns:1fr 1fr}}.control.search{{grid-column:1/-1}}.event-grid{{grid-template-columns:1fr}}}}
@media(max-width:480px){{h1{{font-size:44px}}.stats{{gap:8px}}.stat{{padding:15px}}.stat strong{{font-size:28px}}.filters{{grid-template-columns:1fr}}.control.search{{grid-column:auto}}.event-body{{padding:16px}}.event-card h3{{font-size:21px}}.card-footer{{align-items:flex-start;flex-direction:column}}}}
.hero-actions{{display:flex;flex-wrap:wrap;gap:10px;margin-top:26px}}.button-link{{display:inline-flex;align-items:center;justify-content:center;min-height:46px;padding:0 17px;border-radius:999px;background:var(--gold);color:#173b3f;font-size:14px;font-weight:850;text-decoration:none}}.button-link.secondary{{background:transparent;color:#fff;border:1px solid rgba(255,255,255,.45)}}.about{{scroll-margin-top:24px;margin-top:64px;padding:36px;border-radius:24px;background:var(--green);color:#fff;box-shadow:var(--shadow)}}.about-grid{{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(260px,.65fr);gap:36px;align-items:start}}.about-kicker{{display:block;color:#ffd58d;font-size:11px;font-weight:900;letter-spacing:.13em;text-transform:uppercase;margin-bottom:8px}}.about h2{{font-family:Georgia,serif;font-size:clamp(31px,5vw,46px);line-height:1.05;margin:0 0 16px}}.about p{{color:#d8e7e5;line-height:1.65;margin:0 0 14px}}.about-card{{padding:24px;border:1px solid rgba(255,255,255,.17);border-radius:18px;background:rgba(255,255,255,.06)}}.about-card h3{{font-family:Georgia,serif;font-size:24px;margin:0 0 10px}}.about-links{{display:flex;flex-direction:column;align-items:flex-start;gap:10px;margin-top:18px}}.about-links a{{color:#ffd58d;font-weight:800;text-underline-offset:3px}}.about-privacy{{font-size:12px;color:#bcd2d0!important;margin-top:18px!important}}footer nav{{display:inline-flex;flex-wrap:wrap;justify-content:center;gap:12px;margin-left:8px}}
@media(max-width:800px){{.about{{padding:28px 22px}}.about-grid{{grid-template-columns:1fr;gap:22px}}}}
</style></head><body>
<header class='hero'><div class='hero-inner'><div class='brand'><span class='brand-mark'>W</span> Warsaw Weekend</div><h1>Find something worth going to.</h1><p class='hero-copy'>A Warsaw-first guide to concerts, markets, classes, family activities, festivals, sports, and community events across northern Indiana.</p><div class='hero-actions'><a class='button-link' href='{COMMUNITY_FORM_URL}' target='_blank' rel='noopener noreferrer'>Submit an event</a><a class='button-link secondary' href='#about'>About the project</a></div><p class='updated'>Updated {now:%A, %B %d at %I:%M %p} · {checked} sources checked · {contributing} contributing{f' · {pending_sources} integration pending' if pending_sources else ''}</p></div></header>
<div class='stats'><div class='stat'><strong>{local_count}</strong><span>Warsaw & Winona Lake</span></div><div class='stat'><strong>{nearby_count}</strong><span>within 25 miles</span></div><div class='stat'><strong>{week_count}</strong><span>in the next 7 days</span></div><div class='stat'><strong>{len(events)}</strong><span>upcoming events</span></div></div>
<main><div class='filters' aria-label='Event filters'><div class='control search'><label for='search'>Search</label><input id='search' type='search' placeholder='Music, markets, Warsaw…'></div><div class='control'><label for='distance'>Distance</label><select id='distance'><option value='all'>Everywhere</option><option value='local'>Warsaw & Winona Lake</option><option value='nearby'>Within 25 miles</option><option value='regional'>Within 50 miles</option></select></div><div class='control'><label for='date-range'>When</label><select id='date-range'><option value='all'>Any date</option><option value='7'>Next 7 days</option><option value='14'>Next 2 weeks</option><option value='30'>Next 30 days</option></select></div><div class='control'><label for='category'>Category</label><select id='category'><option value='all'>All categories</option>{options}</select></div></div>
<div class='results-bar'><span id='result-count'>{len(events)} events shown</span><button id='clear' type='button'>Clear filters</button></div>{sections}<div class='empty' id='empty'><h2>No events match those filters.</h2><p>Try a wider distance, date range, or a shorter search.</p></div>
<section class='about' id='about'><div class='about-grid'><div><span class='about-kicker'>Why this exists</span><h2>One useful calendar for the Warsaw area.</h2><p>Event details are scattered across venue sites, library calendars, tourism pages, social posts, and word of mouth. Warsaw Weekend brings those public listings into one searchable place, puts events closest to Warsaw first, and links back to the original source so details can be confirmed.</p><p>Community members and organizers can submit missing events. Every submission is reviewed before it reaches the public calendar, and approval publishes only event information&mdash;never the submitter's email or private review notes.</p></div><aside class='about-card'><span class='about-kicker'>About the creator</span><h3>Hi, I&rsquo;m Arseniy.</h3><p>I created Warsaw Weekend to make it easier to discover what is happening close to home without checking a dozen different calendars.</p><div class='about-links'><a href='https://github.com/agr77one' target='_blank' rel='noopener noreferrer'>Connect with me on GitHub</a><a href='https://github.com/agr77one/warsaw-events/issues' target='_blank' rel='noopener noreferrer'>Send a correction or idea</a><a href='{COMMUNITY_FORM_URL}' target='_blank' rel='noopener noreferrer'>Submit an event for review</a></div><p class='about-privacy'>Submission contact details stay in the private moderation workbook and are not published.</p></aside></div></section></main>
<footer>Built from public event calendars. Always confirm time, admission, and availability with the linked source.<nav aria-label='Project links'><a href='#about'>About</a><a href='{COMMUNITY_FORM_URL}' target='_blank' rel='noopener noreferrer'>Submit an event</a><a href='https://github.com/agr77one/warsaw-events' target='_blank' rel='noopener noreferrer'>View the project</a></nav></footer>
<script>
const controls={{search:document.querySelector('#search'),distance:document.querySelector('#distance'),days:document.querySelector('#date-range'),category:document.querySelector('#category')}};
const cards=[...document.querySelectorAll('[data-event]')];
const sections=[...document.querySelectorAll('[data-section]')];
function applyFilters(){{
 const term=controls.search.value.trim().toLowerCase(); const distance=controls.distance.value; const days=controls.days.value; const category=controls.category.value;
 let visible=0;
 cards.forEach(card=>{{const zone=card.dataset.zone; const zoneOk=distance==='all'||(distance==='local'&&zone==='local')||(distance==='nearby'&&zone!=='regional')||(distance==='regional'); const dayOk=days==='all'||Number(card.dataset.days)<=Number(days); const categoryOk=category==='all'||card.dataset.category===category; const searchOk=!term||card.dataset.search.includes(term); const show=zoneOk&&dayOk&&categoryOk&&searchOk; card.hidden=!show; if(show)visible++;}});
 sections.forEach(section=>{{section.hidden=!section.querySelector('[data-event]:not([hidden])')}}); document.querySelector('#result-count').textContent=`${{visible}} event${{visible===1?'':'s'}} shown`; document.querySelector('#empty').style.display=visible?'none':'block';
}}
Object.values(controls).forEach(control=>control.addEventListener(control.tagName==='INPUT'?'input':'change',applyFilters));
document.querySelector('#clear').addEventListener('click',()=>{{controls.search.value='';controls.distance.value='all';controls.days.value='all';controls.category.value='all';applyFilters();}});
</script></body></html>"""


def send_email(subject: str, html_body: str, markdown_body: str) -> bool:
    username = os.getenv("EMAIL_USERNAME")
    password = os.getenv("EMAIL_APP_PASSWORD")
    recipient_value = os.getenv("EMAIL_TO", "")
    recipients = [
        address for _, address in getaddresses([recipient_value.replace(";", ",")])
        if "@" in address
    ]
    if not username or not password or not recipients:
        print("Email secrets not configured; skipping email")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = username
    msg["To"] = username
    msg["Bcc"] = ", ".join(recipients)
    msg.set_content(markdown_body)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(username, password)
        smtp.send_message(msg, to_addrs=recipients)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "newsletter", "manual"], default="manual")
    args = parser.parse_args()
    now = datetime.now(WARSAW_TIMEZONE).replace(tzinfo=None)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    crawled, health = crawl(conn, now)
    for event in crawled:
        upsert_event(conn, event, now.isoformat())
    conn.commit()
    events = query_events(conn, now)
    changes = recent_changes(conn, now, 30 if args.mode == "daily" else 168)
    export_csv(events)
    (OUTPUT / "events.json").write_text(json.dumps(events, indent=2), encoding="utf-8")
    (OUTPUT / "source_health.json").write_text(json.dumps(health, indent=2), encoding="utf-8")
    markdown, email_html = render_newsletter(events, changes, now)
    (OUTPUT / "weekly_newsletter.md").write_text(markdown, encoding="utf-8")
    (OUTPUT / "weekly_newsletter.html").write_text(email_html, encoding="utf-8")
    (DOCS / "index.html").write_text(render_portal(events, health, now), encoding="utf-8")
    alerts = sorted(
        [x for x in changes if x.get("importance", 0) >= 7 or x.get("change_type") in {"CANCELED", "SOLD_OUT"}],
        key=lambda item: (-item.get("importance", 0), item.get("start", ""), item.get("title", "")),
    )[:100]
    (OUTPUT / "daily_alerts.json").write_text(json.dumps(alerts, indent=2), encoding="utf-8")
    if args.mode == "newsletter":
        send_email(f"Warsaw events newsletter · {now:%B %d}", email_html, markdown)
    print(json.dumps({"mode": args.mode, "crawled": len(crawled), "stored": len(events), "changes": len(changes), "alerts": len(alerts), "email_configured": bool(os.getenv('EMAIL_USERNAME'))}, indent=2))


if __name__ == "__main__":
    main()
