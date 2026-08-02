from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from pipeline import (
    Event,
    extract_detail_admission,
    extract_detail_page,
    extract_ics,
    extract_jsonld,
    extract_librarycalendar_feed,
    fetch_text,
    load_sources,
    normalized_title,
)

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
EXPECTED_2026 = (
    ("Warsaw Community Public Library", "Family Dino Crafts", "2026-06-01T10:30:00", "2026-06-01T19:00:00"),
    ("Warsaw Community Public Library", "Story Emporium Used Bookstore", "2026-06-02T16:00:00", "2026-06-02T19:00:00"),
    ("Lake City Skiers", "Home Show", "2026-06-02T18:30:00", "2026-06-02T19:30:00"),
    ("Lake City Skiers", "City Of Lakes Tournament", "2026-06-20T00:00:00", "2026-06-21T00:00:00"),
    ("Lake City Skiers", "Learn to ski clinic", "2026-07-25T10:00:00", "2026-07-25T13:00:00"),
    ("Wagon Wheel Center", "CATS", "2026-06-04T00:00:00", "2026-06-13T23:59:59"),
    ("Wagon Wheel Center", "RODGERS & HAMMERSTEIN'S CAROUSEL", "2026-06-18T00:00:00", "2026-06-27T23:59:59"),
    ("Wagon Wheel Center", "FOOTLOOSE", "2026-07-02T00:00:00", "2026-07-11T23:59:59"),
    ("Wagon Wheel Center", "DIAL M FOR MURDER", "2026-07-16T00:00:00", "2026-07-25T23:59:59"),
    ("Wagon Wheel Center", "COLE PORTER'S ANYTHING GOES", "2026-07-30T00:00:00", "2026-08-08T23:59:59"),
    ("Kosciusko County Fair", "2026 Annual Fair Parade", "2026-07-12T14:00:00", "2026-07-12T16:00:00"),
    ("Kosciusko County Fair", "2026 Demolition Derby Night", "2026-07-17T19:00:00", "2026-07-17T21:30:00"),
)


def audit_bounds(as_of: date) -> tuple[date, date]:
    previous_month_end = as_of.replace(day=1) - timedelta(days=1)
    two_months_back_end = previous_month_end.replace(day=1) - timedelta(days=1)
    return two_months_back_end.replace(day=1), previous_month_end


def in_range(event: Event, start: date, end: date) -> bool:
    return start <= datetime.fromisoformat(event.start).date() <= end


def unique(events: list[Event]) -> list[Event]:
    result: dict[tuple[str, str, str], Event] = {}
    for event in events:
        result[(event.source_name, normalized_title(event.title), event.start)] = event
    return sorted(result.values(), key=lambda item: (item.start, item.source_name, item.title))


def library_census(source: dict, start: date, end: date) -> tuple[list[Event], dict]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)

    def fetch_day(day: date) -> tuple[int, list[Event]]:
        url = (
            "https://warsaw.librarycalendar.com/events/feed/html"
            f"?current_date={day.isoformat()}&ongoing_events=hide"
        )
        with httpx.Client(timeout=25, follow_redirects=True) as client:
            markup = fetch_text(client, url)
        cards = len(BeautifulSoup(markup, "html.parser").select("article.event-card"))
        return cards, extract_librarycalendar_feed(markup, {**source, "url": url})

    records: list[Event] = []
    official_count = 0
    failed_days: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(fetch_day, day): day for day in days}
        for job in as_completed(jobs):
            try:
                cards, events = job.result()
                official_count += cards
                records.extend(events)
                if cards != len(events):
                    failed_days.append(jobs[job].isoformat())
            except Exception:
                failed_days.append(jobs[job].isoformat())
    records = unique(records)
    return records, {
        "source": source["name"], "official_records": official_count,
        "extracted_records": len(records), "failed_dates": sorted(failed_days),
    }


def ics_census(source: dict, start: date, end: date) -> tuple[list[Event], dict]:
    with httpx.Client(timeout=25, follow_redirects=True) as client:
        records = [
            event for event in extract_ics(fetch_text(client, source["url"]), source)
            if in_range(event, start, end)
        ]
    records = unique(records)
    return records, {
        "source": source["name"], "official_records": len(records),
        "extracted_records": len(records), "failed_dates": [],
    }


def linked_history_census(source: dict, start: date, end: date) -> tuple[list[Event], dict]:
    links = list(source.get("history_urls", []))
    if source["name"] == "Kosciusko County Fair" and links:
        with httpx.Client(timeout=25, follow_redirects=True) as client:
            archive = fetch_text(client, links[0])
        for anchor in BeautifulSoup(archive, "html.parser").select("a[href*='/events/2026/']"):
            url = urljoin(links[0], anchor.get("href", ""))
            if url not in links:
                links.append(url)
    records: list[Event] = []
    failed_urls: list[str] = []
    with httpx.Client(timeout=25, follow_redirects=True) as client:
        for url in links:
            try:
                markup = fetch_text(client, url)
                extracted = extract_jsonld(markup, source) or extract_detail_page(markup, source, url)
                published_admission = extract_detail_admission(markup)
                for event in extracted:
                    event.admission = event.admission or published_admission
                records.extend(event for event in extracted if in_range(event, start, end))
            except Exception:
                failed_urls.append(url)
    records = unique(records)
    return records, {
        "source": source["name"], "official_records": len(records),
        "extracted_records": len(records), "pages_checked": len(links), "failed_urls": failed_urls,
    }


def check_expected(events: list[Event], start: date, end: date) -> list[dict]:
    applicable = [item for item in EXPECTED_2026 if start <= datetime.fromisoformat(item[2]).date() <= end]
    checks: list[dict] = []
    for source, title, expected_start, expected_end in applicable:
        match = next((
            event for event in events
            if event.source_name == source
            and normalized_title(event.title) == normalized_title(title)
            and event.start == expected_start
        ), None)
        checks.append({
            "source": source, "title": title, "expected_start": expected_start,
            "expected_end": expected_end, "found": bool(match),
            "end_matches": bool(match and match.end == expected_end),
            "venue_present": bool(match and match.venue),
            "link_present": bool(match and match.event_url),
        })
    return checks


def build_report(as_of: date) -> dict:
    start, end = audit_bounds(as_of)
    sources = {source["name"]: source for source in load_sources()}
    audits = (
        library_census(sources["Warsaw Community Public Library"], start, end),
        ics_census(sources["Lake City Skiers"], start, end),
        linked_history_census(sources["Wagon Wheel Center"], start, end),
        linked_history_census(sources["Kosciusko County Fair"], start, end),
    )
    events = unique([event for source_events, _ in audits for event in source_events])
    required_fields = ("title", "start", "venue", "city", "state", "event_url")
    optional_fields = ("end", "description", "admission", "image_url")
    completeness = {
        field: {
            "present": sum(bool(getattr(event, field)) for event in events),
            "total": len(events),
        }
        for field in required_fields + optional_fields
    }
    expected = check_expected(events, start, end)
    return {
        "as_of": as_of.isoformat(), "range_start": start.isoformat(), "range_end": end.isoformat(),
        "summary": {
            "official_records": sum(item[1]["official_records"] for item in audits),
            "unique_extracted_records": len(events),
            "required_fields_complete": all(
                completeness[field]["present"] == completeness[field]["total"] for field in required_fields
            ),
            "expected_checks_passed": sum(item["found"] and item["end_matches"] for item in expected),
            "expected_checks_total": len(expected),
        },
        "sources": [item[1] for item in audits], "field_completeness": completeness,
        "expected_event_checks": expected, "events": [asdict(event) for event in events],
    }


def write_report(report: dict) -> None:
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "historical_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = report["summary"]
    lines = [
        "# Historical scraper validation", "",
        f"Audit date: {report['as_of']}",
        f"Historical window: {report['range_start']} through {report['range_end']}", "",
        f"- Official calendar records inspected: {summary['official_records']}",
        f"- Unique records extracted: {summary['unique_extracted_records']}",
        f"- Required fields complete: {'yes' if summary['required_fields_complete'] else 'no'}",
        f"- Known historical checks passed: {summary['expected_checks_passed']}/{summary['expected_checks_total']}", "",
        "## Source census", "",
    ]
    for source in report["sources"]:
        lines.append(
            f"- {source['source']}: {source['extracted_records']} extracted from "
            f"{source['official_records']} official records"
        )
    lines += ["", "## Field completeness", ""]
    lines.append("Required fields are title, start, venue, city, state, and source link. End time, description, admission, and image are reported only when the official source publishes them.")
    lines.append("")
    for field, counts in report["field_completeness"].items():
        lines.append(f"- {field}: {counts['present']}/{counts['total']}")
    lines += ["", "## Known-event checks", ""]
    for item in report["expected_event_checks"]:
        passed = item["found"] and item["end_matches"] and item["venue_present"] and item["link_present"]
        lines.append(f"- {'PASS' if passed else 'FAIL'} — {item['source']}: {item['title']}")
    (OUTPUT / "historical_validation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the previous two calendar months against official archives.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    report = build_report(args.as_of)
    write_report(report)
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
