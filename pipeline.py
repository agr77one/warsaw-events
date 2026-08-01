from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import smtplib
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
from urllib.parse import urljoin

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
INCLUDE_TERMS = (
    "fair", "festival", "carnival", "parade", "fireworks", "boat", "flotilla",
    "water ski", "waterski", "car show", "cruise-in", "rodeo", "tractor pull",
    "demolition derby", "art fair", "craft fair", "concert", "theater", "musical",
    "museum", "exhibit", "food festival", "market", "5k", "10k", "triathlon",
    "cycling", "bike ride", "community celebration", "heritage days",
)
EXCLUDE_TERMS = (
    "movie night", "story time", "book club", "support group", "networking",
    "chamber meeting", "routine class", "restaurant special", "trivia night",
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
    "goshen": 30,
    "wabash": 33,
    "elkhart": 41,
    "shipshewana": 44,
    "fort wayne": 45,
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


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", value).strip() or None


def readable_text(value: str | None) -> str | None:
    if value is None:
        return None
    decoded = html.unescape(value)
    text = BeautifulSoup(decoded, "html.parser").get_text(" ", strip=True)
    return clean(text)


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
                admission = f"{offers.get('priceCurrency', '$')} {offers.get('price')}"
            desc = BeautifulSoup(str(item.get("description", "")), "html.parser").get_text(" ", strip=True)
            events.append(Event(
                title=clean(item.get("name")) or "Untitled event",
                start=start_dt.isoformat(),
                end=parse_date(item.get("endDate")).isoformat() if parse_date(item.get("endDate")) else None,
                venue=clean(location.get("name")) if isinstance(location, dict) else None,
                address=clean(street), city=clean(city), state=clean(state),
                description=clean(desc) or "",
                admission=clean(admission), source_name=source["name"],
                source_url=source["url"], event_url=item.get("url") or source["url"],
                confidence="A" if source.get("reliability") in {"official", "tourism"} else "B",
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
        start_dt = parse_date(date_el.get("datetime") or date_el.get_text(" ", strip=True))
        if not start_dt:
            continue
        a = title_el.find("a") if title_el.name != "a" else title_el
        href = urljoin(source["url"], a.get("href")) if a and a.get("href") else source["url"]
        events.append(Event(
            title=clean(title_el.get_text(" ", strip=True)) or "Untitled event",
            start=start_dt.isoformat(), end=None, venue=None, address=None, city=None, state="IN",
            description=clean(block.get_text(" ", strip=True)) or "",
            admission=None, source_name=source["name"], source_url=source["url"], event_url=href,
            confidence="A" if source.get("reliability") in {"official", "tourism"} else "B",
            distance_miles=source.get("distance_miles"),
        ))
    return events


def proximity_bonus(distance_miles: float | None) -> int:
    if distance_miles is None:
        return 0
    for maximum_distance, bonus in PROXIMITY_BANDS:
        if distance_miles <= maximum_distance:
            return bonus
    return 0


def filter_and_score(event: Event, now: datetime) -> Event | None:
    text = f"{event.title} {event.description}".lower()
    if any(term in text for term in EXCLUDE_TERMS):
        return None
    if not any(term in text for term in INCLUDE_TERMS):
        return None
    start = datetime.fromisoformat(event.start)
    if start < now - timedelta(days=1) or start > now + timedelta(days=180):
        return None
    city = (event.city or "").casefold().strip()
    event.distance_miles = CITY_DISTANCE_MILES.get(city, event.distance_miles)
    event.importance = (4 if event.confidence == "A" else 2)
    event.importance += 4 if any(term in text for term in IMPORTANT_TERMS) else 0
    event.importance += 1 if event.venue else 0
    event.importance += 1 if event.city or event.address else 0
    event.importance += proximity_bonus(event.distance_miles)
    key = "|".join([event.title.lower(), start.strftime("%Y-%m-%d"), (event.city or "").lower()])
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
            response = client.get(source["url"])
            response.raise_for_status()
            extracted = extract_jsonld(response.text, source)
            if not extracted:
                extracted = extract_generic(response.text, source)
            accepted = [e for e in (filter_and_score(x, now) for x in extracted) if e]
            found.extend(accepted)
            status, error = "ok", None
        except Exception as exc:
            accepted, status, error = [], "failed", str(exc)
        health.append({"name": source["name"], "url": source["url"], "status": status,
                       "event_count": len(accepted), "error": error})
        conn.execute("INSERT INTO source_runs(source_name,source_url,checked_at,status,event_count,error) VALUES(?,?,?,?,?,?)",
                     (source["name"], source["url"], now.isoformat(), status, len(accepted), error))
    conn.commit()
    return dedupe(found), health


def query_events(conn: sqlite3.Connection, now: datetime, days: int = 120) -> list[dict]:
    rows = conn.execute("SELECT payload_json FROM events WHERE start>=? AND start<? ORDER BY start,title",
                        (now.isoformat(), (now + timedelta(days=days)).isoformat())).fetchall()
    events = [json.loads(row[0]) for row in rows]
    for event in events:
        for field in ("title", "venue", "address", "city", "description", "admission"):
            event[field] = readable_text(event.get(field))
    return sorted(events, key=lambda event: (
        event["start"], -event.get("importance", 0), event["title"],
    ))


def recent_changes(conn: sqlite3.Connection, now: datetime, hours: int) -> list[dict]:
    rows = conn.execute("SELECT change_type,after_json FROM changes WHERE detected_at>=? ORDER BY detected_at DESC",
                        ((now - timedelta(hours=hours)).isoformat(),)).fetchall()
    result = []
    for change_type, payload in rows:
        item = json.loads(payload) if payload else {}
        item["change_type"] = change_type
        result.append(item)
    return result


def export_csv(events: list[dict]) -> None:
    fields = ["title","start","end","venue","address","city","state","description","admission",
              "source_name","source_url","event_url","confidence","distance_miles","status",
              "importance","fingerprint"]
    with (OUTPUT / "events.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(events)


def render_newsletter(events: list[dict], changes: list[dict], now: datetime) -> tuple[str, str]:
    grouped: dict[str, list[dict]] = {}
    for event in events:
        dt = datetime.fromisoformat(event["start"])
        if dt > now + timedelta(days=14):
            continue
        key = dt.strftime("%B %Y")
        grouped.setdefault(key, []).append(event)
    md = ["# Warsaw-Area Events Newsletter", "", f"Generated: {now.strftime('%B %d, %Y')}", ""]
    html_parts = ["<h1>Warsaw-Area Events Newsletter</h1>", f"<p>Generated {now:%B %d, %Y}</p>"]
    for month, items in grouped.items():
        md += [f"# {month}", ""]
        html_parts.append(f"<h2>{html.escape(month)}</h2>")
        for event in sorted(items, key=lambda item: (
            -item.get("importance", 0), item["start"], item["title"],
        )):
            dt = datetime.fromisoformat(event["start"])
            location = ", ".join(x for x in [event.get("venue"), event.get("address"), event.get("city"), event.get("state")] if x)
            status = event.get("status", "CONFIRMED")
            md += [f"## {dt:%A, %B %d} · {event['title']}", f"**Time:** {dt:%I:%M %p}",
                   f"**Location:** {location or 'Details pending'}", event.get("description") or "",
                   f"**Admission:** {event.get('admission') or 'Not published'}",
                   f"**Status:** {status}", f"**Source:** {event.get('event_url')}", ""]
            html_parts.append(f"<article><h3>{html.escape(dt.strftime('%A, %B %d'))} · {html.escape(event['title'])}</h3>"
                              f"<p><strong>Time:</strong> {html.escape(dt.strftime('%I:%M %p'))}<br>"
                              f"<strong>Location:</strong> {html.escape(location or 'Details pending')}<br>"
                              f"<strong>Admission:</strong> {html.escape(event.get('admission') or 'Not published')}<br>"
                              f"<strong>Status:</strong> {html.escape(status)}</p>"
                              f"<p>{html.escape(event.get('description') or '')}</p>"
                              f"<p><a href='{html.escape(event.get('event_url') or event.get('source_url'))}'>Official source</a></p></article>")
    if changes:
        md += ["# Changes", ""] + [f"- **{x['change_type']}** {x.get('title')}" for x in changes]
        html_parts.append("<h2>Changes</h2><ul>" + "".join(
            f"<li><strong>{html.escape(x['change_type'])}</strong> {html.escape(x.get('title',''))}</li>" for x in changes) + "</ul>")
    return "\n".join(md), "<!doctype html><html><body style='font-family:Arial;max-width:800px;margin:auto;padding:24px'>" + "".join(html_parts) + "</body></html>"


def render_portal(events: list[dict], health: list[dict], now: datetime) -> str:
    cards = []
    ranked_events = sorted(events, key=lambda event: (
        -event.get("importance", 0), event["start"], event["title"],
    ))
    for event in ranked_events:
        dt = datetime.fromisoformat(event["start"])
        location = ", ".join(x for x in [event.get("venue"), event.get("city"), event.get("state")] if x)
        distance = event.get("distance_miles")
        proximity = "Warsaw area" if distance is not None and distance <= 10 else (
            f"about {distance:g} miles from Warsaw" if distance is not None else "distance pending"
        )
        cards.append(f"<article class='card' data-text='{html.escape((event['title']+' '+location).lower())}'>"
                     f"<div class='date'>{dt:%b %d}</div><h2>{html.escape(event['title'])}</h2>"
                     f"<p>{html.escape(location or 'Location pending')} · {html.escape(proximity)}</p>"
                     f"<p>{html.escape((event.get('description') or '')[:280])}</p>"
                     f"<a href='{html.escape(event.get('event_url') or event.get('source_url'))}'>Official source</a></article>")
    ok = sum(x["status"] == "ok" for x in health)
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
<title>Warsaw Area Events</title><style>body{{font-family:system-ui;margin:0;background:#f5f6f8;color:#1d2430}}header{{background:#17233b;color:white;padding:32px}}main{{max-width:1000px;margin:auto;padding:24px}}input{{width:100%;padding:14px;font-size:16px;box-sizing:border-box;margin:16px 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}}.card{{background:white;padding:18px;border-radius:12px;box-shadow:0 2px 9px #0001}}.date{{font-weight:700;color:#53657d}}a{{color:#174ea6}}</style></head><body>
<header><h1>Warsaw Area Events</h1><p>Updated {now:%B %d, %Y %I:%M %p} · {len(events)} upcoming events · {ok}/{len(health)} sources reachable</p></header>
<main><input id='q' placeholder='Search events or locations'><div class='grid'>{''.join(cards)}</div></main>
<script>q.oninput=()=>document.querySelectorAll('.card').forEach(x=>x.style.display=x.dataset.text.includes(q.value.toLowerCase())?'block':'none')</script></body></html>"""


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
    now = datetime.now().astimezone().replace(tzinfo=None)
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
    )
    (OUTPUT / "daily_alerts.json").write_text(json.dumps(alerts, indent=2), encoding="utf-8")
    if args.mode == "newsletter":
        send_email(f"Warsaw events newsletter · {now:%B %d}", email_html, markdown)
    print(json.dumps({"mode": args.mode, "crawled": len(crawled), "stored": len(events), "changes": len(changes), "alerts": len(alerts), "email_configured": bool(os.getenv('EMAIL_USERNAME'))}, indent=2))


if __name__ == "__main__":
    main()
