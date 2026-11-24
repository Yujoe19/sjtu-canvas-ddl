import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import canvas_session
import check_deadlines as deadlines


class DeadlineTests(unittest.TestCase):
    now = datetime(2030, 1, 1, 21, tzinfo=deadlines.BEIJING)

    def assignment(self, delta, **fields):
        due = (self.now + delta).astimezone(timezone.utc)
        row = {"id": 2, "name": "Example", "due_at": due.isoformat(), **fields}
        return deadlines.assignment_item({"id": 1, "name": "Course"}, row, self.now)

    def test_window_and_urgency(self):
        for hours, expected in [(0, None), (24, "within_24h"), (25, "upcoming"),
                                (168, "upcoming"), (169, None)]:
            with self.subTest(hours=hours):
                item = self.assignment(timedelta(hours=hours))
                self.assertEqual(item["stage"] if item else None, expected)

    def test_excludes_submitted_and_excused(self):
        for submission in [{"submitted_at": self.now.isoformat()}, {"excused": True},
                           {"workflow_state": "pending_review"}]:
            with self.subTest(submission=submission):
                self.assertIsNone(self.assignment(timedelta(days=1), submission=submission))

    def test_confirmed_reminder_and_changed_date(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(deadlines, "DATA", root), patch.object(deadlines, "PENDING", root / "pending.json"):
                item = self.assignment(timedelta(days=3))
                alerts, receipt = deadlines.prepare(1, [item], [])
                self.assertEqual(len(alerts), 1)
                self.assertEqual(deadlines.prepare(1, [item], [])[1], receipt)
                deadlines.acknowledge(receipt)
                self.assertEqual(deadlines.prepare(1, [item], []), ([], None))
                changed = self.assignment(timedelta(days=2))
                alerts, _ = deadlines.prepare(1, [changed], [])
                self.assertEqual(alerts[0]["reason"], "截止时间变更")

    def test_only_canvas_cookies_are_retained(self):
        cookies = [{"domain": host, "name": "example", "value": "fixture"}
                   for host in [canvas_session.HOST, "jaccount.sjtu.edu.cn", ".sjtu.edu.cn"]]
        state = canvas_session.canvas_state({"cookies": cookies, "origins": ["example"]})
        self.assertEqual(state, {"cookies": [cookies[0]], "origins": []})


if __name__ == "__main__":
    unittest.main()
