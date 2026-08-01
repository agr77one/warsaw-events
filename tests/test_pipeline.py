import sqlite3
import unittest

from pipeline import Event, init_db, upsert_event


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


if __name__ == "__main__":
    unittest.main()
