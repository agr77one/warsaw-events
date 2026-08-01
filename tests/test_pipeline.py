import sqlite3
import smtplib
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from pipeline import (
    Event,
    filter_and_score,
    init_db,
    proximity_bonus,
    query_events,
    render_newsletter,
    render_portal,
    send_email,
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
