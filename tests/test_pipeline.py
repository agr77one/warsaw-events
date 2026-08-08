import json
import sqlite3
import smtplib
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from pipeline import (
    Event,
    categorize,
    crawl,
    dedupe_event_dicts,
    extract_approved_submissions,
    extract_allevents,
    extract_detail_page,
    extract_detail_admission,
    extract_facebook_graph,
    extract_generic,
    extract_ics,
    extract_librarycalendar_feed,
    extract_paginated_generic,
    filter_and_score,
    init_db,
    parse_event_dates,
    proximity_bonus,
    query_events,
    reconcile_community_submissions,
    recent_changes,
    render_newsletter,
    render_portal,
    send_email,
    SourceNotConfigured,
    upsert_event,
)


class UpsertEventTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_db(self.conn)
        self.event = Event(
            title="Warsaw Summer Festival",
            start="2026-08-15T18:00:00",
            end=None,
            venue="Central Park",
            address="123 Main Street",
            city="Warsaw",
            state="IN",
            description="Community festival",
            admission="Free",
            source_name="Test source",
            source_url="https://example.com/events",
            event_url="https://example.com/events/festival",
            confidence="A",
            importance=9,
            fingerprint="event-fingerprint",
        )

    def tearDown(self):
        self.conn.close()

    def test_inserts_event_with_matching_schema(self):
        result = upsert_event(self.conn, self.event, "2026-08-01T12:00:00")

        self.assertEqual(result, "NEW")
        row = self.conn.execute(
            "SELECT title, first_seen, last_seen FROM events WHERE fingerprint=?",
            (self.event.fingerprint,),
        ).fetchone()
        self.assertEqual(
            row,
            ("Warsaw Summer Festival", "2026-08-01T12:00:00", "2026-08-01T12:00:00"),
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0], 1)

    def test_priority_only_change_does_not_create_update_notice(self):
        upsert_event(self.conn, self.event, "2026-08-01T12:00:00")
        self.event.importance = 13
        self.event.distance_miles = 0

        result = upsert_event(self.conn, self.event, "2026-08-01T13:00:00")

        self.assertEqual(result, "UNCHANGED")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0], 1)

    def test_query_output_decodes_entities_and_removes_html(self):
        self.event.title = "Children&#8217;s Festival"
        self.event.description = "&lt;p&gt;Free &amp;amp; open to all&lt;/p&gt;"
        upsert_event(self.conn, self.event, "2026-08-01T12:00:00")

        events = query_events(self.conn, datetime(2026, 8, 1, 12))

        self.assertEqual(events[0]["title"], "Children’s Festival")
        self.assertEqual(events[0]["description"], "Free & open to all")

    def test_recent_changes_collapses_repeated_updates(self):
        upsert_event(self.conn, self.event, "2026-08-01T12:00:00")
        self.event.title = "Warsaw Summer Festival — Updated"
        upsert_event(self.conn, self.event, "2026-08-01T13:00:00")
        self.event.title = "Warsaw Summer Festival — Final"
        upsert_event(self.conn, self.event, "2026-08-01T14:00:00")

        changes = recent_changes(self.conn, datetime(2026, 8, 1, 15), 24)

        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0]["change_type"], "UPDATED")
        self.assertEqual(changes[0]["title"], "Warsaw Summer Festival — Final")


class ProximityScoringTests(unittest.TestCase):
    def test_proximity_bonus_decreases_with_distance(self):
        self.assertEqual(proximity_bonus(0), 4)
        self.assertEqual(proximity_bonus(10), 4)
        self.assertEqual(proximity_bonus(25), 3)
        self.assertEqual(proximity_bonus(50), 2)
        self.assertEqual(proximity_bonus(75), 1)
        self.assertEqual(proximity_bonus(76), 0)
        self.assertEqual(proximity_bonus(None), 0)

    def test_warsaw_event_scores_higher_than_farther_event(self):
        common = dict(
            title="Community Festival",
            start="2026-08-15T18:00:00",
            end=None,
            venue="Town Park",
            address=None,
            state="IN",
            description="Annual community festival",
            admission="Free",
            source_name="Test source",
            source_url="https://example.com/events",
            event_url="https://example.com/events/festival",
            confidence="A",
        )
        near = Event(city="Warsaw", distance_miles=45, **common)
        farther = Event(city="Fort Wayne", distance_miles=0, **common)
        now = datetime(2026, 8, 1, 12)

        scored_near = filter_and_score(near, now)
        scored_farther = filter_and_score(farther, now)

        self.assertIsNotNone(scored_near)
        self.assertIsNotNone(scored_farther)
        self.assertEqual(scored_near.distance_miles, 0)
        self.assertEqual(scored_farther.distance_miles, 45)
        self.assertGreater(scored_near.importance, scored_farther.importance)

    def test_newsletter_and_portal_put_higher_priority_first(self):
        common = {
            "start": "2026-08-08T18:00:00",
            "venue": "Town Park",
            "city": "Warsaw",
            "state": "IN",
            "description": "Community event",
            "admission": "Free",
            "status": "CONFIRMED",
            "event_url": "https://example.com/event",
            "source_url": "https://example.com/events",
        }
        lower = {**common, "title": "Lower Priority", "importance": 6, "distance_miles": 45}
        higher = {**common, "title": "Higher Priority", "importance": 10, "distance_miles": 0}
        now = datetime(2026, 8, 1, 12)

        markdown, _ = render_newsletter([lower, higher], [], now)
        portal = render_portal([lower, higher], [], now)

        self.assertLess(markdown.index("Higher Priority"), markdown.index("Lower Priority"))
        self.assertLess(portal.index("Higher Priority"), portal.index("Lower Priority"))


class CommunitySubmissionTests(unittest.TestCase):
    def test_approved_feed_maps_sanitized_event_fields(self):
        feed = """Submission ID,Event title,Start date,Start time,End date,End time,Venue name,Street address,City,State,Event description,Admission or price,Official event or ticket URL,Image URL
SUB-00001,Free Movie Day,8/20/2026,6:30:00 PM,8/20/2026,8:30:00 PM,North Pointe Cinemas,1060 Mariners Dr,Warsaw,IN,Free community movie,Free,https://example.com/movie,https://example.com/movie.jpg
"""

        events = extract_approved_submissions(feed)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.start, "2026-08-20T18:30:00")
        self.assertEqual(event.end, "2026-08-20T20:30:00")
        self.assertEqual(event.city, "Warsaw")
        self.assertEqual(event.admission, "Free")
        self.assertEqual(event.confidence, "B")
        self.assertIn("SUB-00001", event.source_url)
        self.assertFalse(hasattr(event, "submitter_email"))

    def test_invalid_or_incomplete_approved_rows_are_skipped(self):
        feed = """Submission ID,Event title,Start date,Start time,End date,End time,Venue name,Street address,City,State,Event description,Admission or price,Official event or ticket URL,Image URL
SUB-00001,Missing link,8/20/2026,6:30 PM,,,Town Hall,1 Main St,Warsaw,IN,Description,Free,not-a-url,
SUB-00002,Missing address,8/20/2026,6:30 PM,,,Town Hall,,Warsaw,IN,Description,Free,https://example.com/event,
"""

        self.assertEqual(extract_approved_submissions(feed), [])

    def test_successful_feed_reconciliation_removes_withdrawn_event(self):
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        event = Event(
            title="Community Movie",
            start="2026-08-20T18:30:00",
            end=None,
            venue="North Pointe Cinemas",
            address="1060 Mariners Dr",
            city="Warsaw",
            state="IN",
            description="Free movie",
            admission="Free",
            source_name="Approved community submissions",
            source_url="https://example.com/form#submission=SUB-00001",
            event_url="https://example.com/movie",
            confidence="B",
            fingerprint="community-event",
        )
        upsert_event(conn, event, "2026-08-01T12:00:00")

        removed = reconcile_community_submissions(conn, [], "2026-08-02T12:00:00")

        self.assertEqual(removed, 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        change = conn.execute(
            "SELECT change_type,after_json FROM changes ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(change[0], "WITHDRAWN")
        self.assertEqual(json.loads(change[1])["status"], "WITHDRAWN")
        conn.close()

    @patch("pipeline.load_sources", return_value=[])
    @patch("pipeline.fetch_text", side_effect=RuntimeError("temporary feed outage"))
    def test_failed_feed_does_not_remove_published_event(self, _fetch_text, _load_sources):
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        event = Event(
            title="Community Movie",
            start="2026-08-20T18:30:00",
            end=None,
            venue="North Pointe Cinemas",
            address="1060 Mariners Dr",
            city="Warsaw",
            state="IN",
            description="Free movie",
            admission="Free",
            source_name="Approved community submissions",
            source_url="https://example.com/form#submission=SUB-00001",
            event_url="https://example.com/movie",
            confidence="B",
            fingerprint="community-event",
        )
        upsert_event(conn, event, "2026-08-01T12:00:00")

        with patch.dict("pipeline.os.environ", {"COMMUNITY_EVENTS_FEED_URL": "https://example.com/feed.csv"}):
            _events, health = crawl(conn, datetime(2026, 8, 2, 12))

        self.assertEqual(health[-1]["status"], "failed")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        conn.close()


class ExpandedCoverageTests(unittest.TestCase):
    def test_calendar_multi_day_range_inherits_year(self):
        start, end = parse_event_dates("Date: July 12 - July 18, 2026")

        self.assertEqual(start, datetime(2026, 7, 12))
        self.assertEqual(end, datetime(2026, 7, 18))

    def test_calendar_date_range_keeps_start_and_end_times(self):
        start, end = parse_event_dates(
            "Saturday, August 29, 2026 at 10:30am - 1:30pm"
        )

        self.assertEqual(start, datetime(2026, 8, 29, 10, 30))
        self.assertEqual(end, datetime(2026, 8, 29, 13, 30))

    def test_generic_calendar_uses_configured_local_defaults(self):
        source = {
            "name": "Warsaw Calendar",
            "url": "https://example.com/events",
            "reliability": "official",
            "distance_miles": 0,
            "city": "Warsaw",
            "state": "IN",
            "venue": "Downtown Warsaw",
            "selectors": {
                "event_block": "a.event",
                "title": "h3",
                "date": ".date",
                "description": ".description",
            },
        }
        markup = """
        <a class="event" href="/events/farmers-market">
          <h3>Farmers Market</h3>
          <span class="date">August 8, 2026 at 9:00 am - 1:00 pm</span>
          <p class="description">Fresh produce and local vendors.</p>
        </a>
        """

        events = extract_generic(markup, source)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].city, "Warsaw")
        self.assertEqual(events[0].venue, "Downtown Warsaw")
        self.assertEqual(events[0].start, "2026-08-08T09:00:00")
        self.assertEqual(events[0].end, "2026-08-08T13:00:00")
        self.assertEqual(events[0].event_url, "https://example.com/events/farmers-market")

    @patch("pipeline.fetch_text")
    def test_paginated_calendar_collects_later_pages(self, fetch_text):
        source = {
            "name": "Library", "url": "https://example.com/events", "reliability": "official",
            "city": "Warsaw", "state": "IN", "distance_miles": 0, "pagination_pages": 3,
            "selectors": {"event_block": "article", "title": "h2", "date": "time"},
        }
        first = '<article><h2><a href="/one">One</a></h2><time>August 3, 2026 at 1:00 pm</time></article>'
        fetch_text.side_effect = [
            '<article><h2><a href="/two">Two</a></h2><time>September 3, 2026 at 1:00 pm</time></article>',
            '<article><h2><a href="/three">Three</a></h2><time>October 3, 2026 at 1:00 pm</time></article>',
        ]

        events = extract_paginated_generic(MagicMock(), first, source)

        self.assertEqual([event.title for event in events], ["One", "Two", "Three"])

    def test_library_history_feed_keeps_time_room_and_description(self):
        markup = '''
        <article class="event-card">
          <h3 class="lc-event__title"><a class="lc-event__link" href="/event/dino" aria-label='View Details - "Family Dino Crafts" on Monday, June 1, 2026 @ 10:30am'>Family Dino Crafts</a></h3>
          <div class="lc-event-info-item--time">10:30am–7:00pm</div>
          <div class="lc-event__room"><strong>Room:</strong> Meeting Room C</div>
          <div class="lc-event-info__item--categories">Youth Program</div>
          <div class="lc-event__body"><p>Free dinosaur crafts for all ages.</p></div>
        </article>
        '''
        source = {"name": "Library", "url": "https://example.com/events", "reliability": "official", "city": "Warsaw", "state": "IN", "distance_miles": 0}

        events = extract_librarycalendar_feed(markup, source)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start, "2026-06-01T10:30:00")
        self.assertEqual(events[0].end, "2026-06-01T19:00:00")
        self.assertEqual(events[0].venue, "Meeting Room C")
        self.assertEqual(events[0].admission, "Free")

    def test_ics_feed_keeps_recurrence_price_and_link(self):
        calendar = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/Indiana/Indianapolis:20260620T100000
DTEND;TZID=America/Indiana/Indianapolis:20260620T130000
RDATE;TZID=America/Indiana/Indianapolis:20260725T100000
SUMMARY:Learn to Ski Clinic
DESCRIPTION:The clinic costs $20.00 per participant.
LOCATION:Hidden Lake
URL;VALUE=URI:https://example.com/clinic
END:VEVENT
END:VCALENDAR"""
        source = {"name": "Lake City Skiers", "url": "https://example.com/feed.ics", "reliability": "official", "city": "Warsaw", "state": "IN", "distance_miles": 3}

        events = extract_ics(calendar, source)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].start, "2026-07-25T10:00:00")
        self.assertEqual(events[1].end, "2026-07-25T13:00:00")
        self.assertEqual(events[0].admission, "$ 20.00")
        self.assertEqual(events[0].event_url, "https://example.com/clinic")

    def test_configured_detail_selectors_keep_fair_time_and_venue(self):
        markup = '''<h1 class="detail">Annual Fair Parade</h1><div class="eventDetailDetailDate">Date: July 12, 2026</div><div class="eventDetailDetailTime">Time: 2:00 PM - 4:00 PM</div><div class="eventDetailDetailLocation">Kosciusko County Fair Grounds</div><div class="copy">Free community parade.</div>'''
        source = {"name": "Fair", "url": "https://example.com/events", "reliability": "official", "city": "Warsaw", "state": "IN", "distance_miles": 0, "detail_selectors": {"title": "h1.detail", "date": ".eventDetailDetailDate", "time": ".eventDetailDetailTime", "venue": ".eventDetailDetailLocation", "description": ".copy"}}

        events = extract_detail_page(markup, source, "https://example.com/parade")

        self.assertEqual(events[0].start, "2026-07-12T14:00:00")
        self.assertEqual(events[0].end, "2026-07-12T16:00:00")
        self.assertEqual(events[0].venue, "Kosciusko County Fair Grounds")
        self.assertEqual(events[0].admission, "Free")

    def test_detail_page_keeps_published_ticket_prices(self):
        markup = """<dl><dt>Ticket Prices:</dt><dd>Adult — $55.00 Senior — $50.00</dd><dt>Pricing includes all fees.</dt></dl><h3>Venue</h3>"""

        admission = extract_detail_admission(markup)

        self.assertEqual(admission, "Adult — $55.00 Senior — $50.00")

    def test_facebook_graph_requires_supported_api_configuration(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SourceNotConfigured):
                extract_facebook_graph(MagicMock(), {"name": "Facebook", "url": "https://facebook.com/page"})

    def test_facebook_graph_maps_event_fields_without_html_scraping(self):
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {"data": [{"id": "123", "name": "Free Movie Day", "description": "Free admission", "start_time": "2026-08-08T10:00:00-04:00", "end_time": "2026-08-08T12:00:00-04:00", "place": {"name": "North Pointe Cinemas", "location": {"street": "1060 Mariners Dr", "city": "Warsaw", "state": "IN"}}, "cover": {"source": "https://example.com/movie.jpg"}}]}
        client = MagicMock()
        client.get.return_value = response
        source = {"name": "North Pointe Cinemas Facebook", "url": "https://facebook.com/NorthPointeCinemas", "reliability": "official", "city": "Warsaw", "state": "IN", "distance_miles": 0}

        with patch.dict("os.environ", {"FACEBOOK_PAGE_ACCESS_TOKEN": "secret", "FACEBOOK_PAGE_ID": "page", "FACEBOOK_GRAPH_API_VERSION": "v1.0"}, clear=True):
            events = extract_facebook_graph(client, source)

        self.assertEqual(events[0].title, "Free Movie Day")
        self.assertEqual(events[0].admission, "Free")
        self.assertEqual(events[0].venue, "North Pointe Cinemas")
        self.assertEqual(events[0].event_url, "https://www.facebook.com/events/123/")
        self.assertEqual(client.get.call_args.kwargs["headers"], {"Authorization": "Bearer secret"})

    def test_comprehensive_sources_keep_normal_local_programs(self):
        event = Event(
            title="Paper Quilling",
            start="2026-08-03T18:00:00",
            end=None,
            venue="Warsaw Community Public Library",
            address=None,
            city="Warsaw",
            state="IN",
            description="Learn a new paper craft.",
            admission="Free",
            source_name="Library",
            source_url="https://example.com",
            event_url="https://example.com/quilling",
            confidence="A",
        )

        self.assertIsNotNone(filter_and_score(event, datetime(2026, 8, 1), False))
        self.assertIsNone(filter_and_score(event, datetime(2026, 8, 1), True))

    def test_categories_cover_everyday_event_types(self):
        self.assertEqual(categorize("Toddler Story Time"), "Family")
        self.assertEqual(categorize("Downtown Farmers Market"), "Food & markets")
        self.assertEqual(categorize("Summer Concert"), "Music & shows")

    def test_allevents_payload_keeps_exact_time_and_price(self):
        markup = r'''<script>
        _this.events_data = [];
        _this.events_data = [{"eventname_raw":"Warsaw Movie Day","start_time_display":"Sat Aug 8 2026 at 02:30 pm","end_time_display":"Sat Aug 8 2026 at 04:30 pm","location":"North Pointe Cinemas","venue":{"street":"1060 Mariners Dr","city":"Warsaw","state":"IN"},"event_url":"https:\/\/example.com\/movie-day","banner_url":"https:\/\/example.com\/movie.jpg","display_price_label":"Free","short_description":"A free family movie.","tickets":{}}];
        </script>'''
        source = {
            "name": "Warsaw events index",
            "url": "https://allevents.in/warsaw-in/all",
            "reliability": "aggregator",
            "distance_miles": 0,
            "city": "Warsaw",
            "state": "IN",
        }

        events = extract_allevents(markup, source)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].start, "2026-08-08T14:30:00")
        self.assertEqual(events[0].end, "2026-08-08T16:30:00")
        self.assertEqual(events[0].admission, "Free")
        self.assertEqual(events[0].venue, "North Pointe Cinemas")
        self.assertEqual(events[0].confidence, "C")

    def test_portal_has_local_sections_and_interactive_filters(self):
        event = {
            "title": "Warsaw Farmers Market",
            "start": "2026-08-08T09:00:00",
            "end": "2026-08-08T13:00:00",
            "venue": "Downtown Warsaw",
            "city": "Warsaw",
            "state": "IN",
            "description": "Fresh produce and local vendors.",
            "admission": "Free",
            "status": "CONFIRMED",
            "event_url": "https://example.com/event",
            "source_url": "https://example.com/events",
            "source_name": "Downtown Warsaw",
            "confidence": "A",
            "category": "Food & markets",
            "distance_miles": 0,
            "importance": 20,
        }

        portal = render_portal([event], [{"status": "ok"}], datetime(2026, 8, 1, 12))

        self.assertIn("Warsaw & Winona Lake", portal)
        self.assertIn("data-zone='local'", portal)
        self.assertIn("id='distance'", portal)
        self.assertIn("id='date-range'", portal)
        self.assertIn("id='category'", portal)
        self.assertIn("Find something worth going to.", portal)
        self.assertIn("id='about'", portal)
        self.assertIn("About the creator", portal)
        self.assertIn("Hi, I&rsquo;m Arseniy.", portal)
        self.assertIn("Submit an event", portal)
        self.assertIn("https://github.com/agr77one", portal)
        self.assertIn("private moderation workbook", portal)

    def test_same_day_title_variants_prefer_official_source(self):
        common = {
            "start": "2026-08-08T09:00:00",
            "city": "Warsaw",
            "state": "IN",
            "description": "",
            "distance_miles": 0,
        }
        community = {
            **common,
            "title": "Farmers Market - Warsaw",
            "confidence": "C",
            "source_name": "Community index",
        }
        official = {
            **common,
            "title": "Farmers Market",
            "confidence": "A",
            "source_name": "Downtown Warsaw",
        }

        deduped = dedupe_event_dicts([community, official])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source_name"], "Downtown Warsaw")


class EmailPrivacyTests(unittest.TestCase):
    @patch("pipeline.smtplib.SMTP_SSL")
    def test_distribution_list_is_sent_by_bcc(self, smtp_ssl):
        smtp = MagicMock()
        smtp_ssl.return_value.__enter__.return_value = smtp
        secrets = {
            "EMAIL_USERNAME": "sender@example.com",
            "EMAIL_APP_PASSWORD": "app-password",
            "EMAIL_TO": "first@example.com, second@example.com",
        }

        with patch.dict("pipeline.os.environ", secrets, clear=True):
            sent = send_email("Subject", "<p>Newsletter</p>", "Newsletter")

        self.assertTrue(sent)
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "sender@example.com")
        self.assertEqual(message["Bcc"], "first@example.com, second@example.com")
        smtp.login.assert_called_once_with("sender@example.com", "app-password")
        self.assertEqual(
            smtp.send_message.call_args.kwargs["to_addrs"],
            ["first@example.com", "second@example.com"],
        )

        transport = smtplib.SMTP()
        transport.ehlo_or_helo_if_needed = MagicMock()
        transport.sendmail = MagicMock(return_value={})
        transport.send_message(
            message,
            to_addrs=["first@example.com", "second@example.com"],
        )
        _, envelope_recipients, wire_message = transport.sendmail.call_args.args[:3]
        self.assertEqual(
            set(envelope_recipients),
            {"first@example.com", "second@example.com"},
        )
        self.assertNotIn(b"\nBcc:", wire_message)


if __name__ == "__main__":
    unittest.main()

