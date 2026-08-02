from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import html
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
from urllib.parse import urljoin
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
        return response.text
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in {403, 406, 429}:
            raise
    command = ["curl", "--location", "--fail", "--silent", "--show-error", "--max-time", "35", url]
    result = subprocess.run(command, check=True, capture_output=True, timeout=40)
    return result.stdout.decode("utf-8", errors="replace")


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
    start_dt, end_dt = parse_event_dates(date_el.get("datetime") or date_el.get_text(" ", strip=True))
    if not start_dt:
        return []
    description_el = soup.select_one(selectors.get("description", ".event-description, .entry-content p"))
    image_el = soup.select_one(selectors.get("image", "main img, article img"))
    title = readable_text(title_el.get_text(" ", strip=True)) or "Untitled event"
    description = readable_text(description_el.get_text(" ", strip=True)) if description_el else ""
    return [Event(
        title=title, start=start_dt.isoformat(), end=end_dt.isoformat() if end_dt else None,
        venue=source.get("venue"), address=source.get("address"), city=source.get("city"),
        state=source.get("state", "IN"), description=description or "", admission=None,
        source_name=source["name"], source_url=source["url"], event_url=event_url,
        confidence=confidence_for(source), category=categorize(title, description or ""),
        image_url=urljoin(event_url, image_el.get("src")) if image_el and image_el.get("src") else None,
        distance_miles=source.get("distance_miles"),
    )]


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
            for event in extracted:
                event.event_url = event_url
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


def crawl(conn: sqlite3.Connection, now: datetime) -> tuple[list[Event], list[dict]]:
    client = httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    found: list[Event] = []
    health: list[dict] = []
    for source in load_sources():
        try:
            html_text = fetch_text(client, source["url"])
            if "allevents.in/" in source["url"]:
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
            status, error = "ok", None
        except Exception as exc:
            accepted, status, error = [], "failed", str(exc)
        health.append({"name": source["name"], "url": source["url"], "status": status,
                       "event_count": len(accepted), "error": error})
        conn.execute("INSERT INTO source_runs(source_name,source_url,checked_at,status,event_count,error) VALUES(?,?,?,?,?,?)",
                     (source["name"], source["url"], now.isoformat(), status, len(accepted), error))
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
        source_label = "Official calendar" if event.get("confidence") == "A" else (
            "Local reporting" if event.get("confidence") == "B" else "Community listing"
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
    ok = sum(x["status"] == "ok" for x in health)
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
</style></head><body>
<header class='hero'><div class='hero-inner'><div class='brand'><span class='brand-mark'>W</span> Warsaw Weekend</div><h1>Find something worth going to.</h1><p class='hero-copy'>A Warsaw-first guide to concerts, markets, classes, family activities, festivals, sports, and community events across northern Indiana.</p><p class='updated'>Updated {now:%A, %B %d at %I:%M %p} · {ok}/{len(health)} sources reached</p></div></header>
<div class='stats'><div class='stat'><strong>{local_count}</strong><span>Warsaw & Winona Lake</span></div><div class='stat'><strong>{nearby_count}</strong><span>within 25 miles</span></div><div class='stat'><strong>{week_count}</strong><span>in the next 7 days</span></div><div class='stat'><strong>{len(events)}</strong><span>upcoming events</span></div></div>
<main><div class='filters' aria-label='Event filters'><div class='control search'><label for='search'>Search</label><input id='search' type='search' placeholder='Music, markets, Warsaw…'></div><div class='control'><label for='distance'>Distance</label><select id='distance'><option value='all'>Everywhere</option><option value='local'>Warsaw & Winona Lake</option><option value='nearby'>Within 25 miles</option><option value='regional'>Within 50 miles</option></select></div><div class='control'><label for='date-range'>When</label><select id='date-range'><option value='all'>Any date</option><option value='7'>Next 7 days</option><option value='14'>Next 2 weeks</option><option value='30'>Next 30 days</option></select></div><div class='control'><label for='category'>Category</label><select id='category'><option value='all'>All categories</option>{options}</select></div></div>
<div class='results-bar'><span id='result-count'>{len(events)} events shown</span><button id='clear' type='button'>Clear filters</button></div>{sections}<div class='empty' id='empty'><h2>No events match those filters.</h2><p>Try a wider distance, date range, or a shorter search.</p></div></main>
<footer>Built from public event calendars. Always confirm time, admission, and availability with the linked source. · <a href='https://github.com/agr77one/warsaw-events'>View the project</a></footer>
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
