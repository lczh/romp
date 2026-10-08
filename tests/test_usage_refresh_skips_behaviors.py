#!/usr/bin/env python3
"""The turn-end usage refresh asks the CLI for the plan's rate limits only, never its transcript scan (2026-10-08).

romp refreshes the account's /usage bars at every turn end by sending the session's own CLI a `get_usage`
control request (e885e85c8, 2026-07-02). Without `skip_behaviors` that request also builds the answer's
`behaviors` section, which the CLI documents as a scan that "reads every transcript touched in the last
seven days". romp never reads that section (_record_usage_snapshot takes rate_limits alone), and the scan
holds the CLI's input while it runs: the hook answer the CLI's next turn waits for and any message sent
meanwhile sit unread until it finishes.

Measured with a real CLI (a fresh lab session, a UserPromptSubmit callback registered as romp registers one,
the request sent at every result as romp sends it): each turn the CLI started from its own queue began only
after the previous result's request finished, 2.6 to 4.2 s per turn on a small machine, and a message sent
behind five queued background-command notifications was answered 21.9 s later; with `skip_behaviors` the
request took 0.03 to 0.05 s, the turns started at once, and the same message was answered in 2.5 s. On a
machine whose last week of transcripts runs to gigabytes the request took 14 to 30 s after every result, so
a session working through queued background-task notifications (one turn each, as the CLI runs them) spent
about half a minute per notification before reaching the user's next message, while showing as idle.

Every string here is invented."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from romp_load import load_source  # noqa: E402

BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load: the backend resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "5e7a1c2d-0b3f-4e6a-9d8c-2f1e0a9b8c7d"     # a private synthetic sid


class _Query:
    """The SDK client's control channel, recording what the session asks the CLI."""

    def __init__(self, answer):
        self.sent = []
        self.answer = answer

    async def _send_control_request(self, req):
        self.sent.append(dict(req))
        return self.answer


class _Client:
    def __init__(self, q):
        self._query = q


class UsageRefreshAsksOnlyForRateLimits(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        (Path(self.d) / "session-hosts").write_text("off\n")   # this root is the test's own: no session host
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        self.s = sb.SdkSession(self.be, {"sid": SID, "name": "api", "cwd": self.d})
        self.s.api_key_auth = False

    def _refresh(self, answer):
        q = _Query(answer)
        self.s.client = _Client(q)
        asyncio.run(self.s._do_refresh_usage())
        return q.sent

    def test_the_refresh_asks_the_cli_to_skip_its_transcript_scan(self):
        sent = self._refresh({"rate_limits": None, "behaviors": None})
        self.assertEqual(sent, [{"subtype": "get_usage", "skip_behaviors": True}],
                         "the request names skip_behaviors: without it the CLI scans every transcript touched in the "
                         "last seven days and holds its next turn and every incoming message until the scan ends")

    def test_the_rate_limits_still_land_without_the_scan(self):
        """skip_behaviors leaves the answer's rate_limits as they are (the CLI nulls only `behaviors`), and those are all
        the snapshot reads, so the bars still update from the same request."""
        self._refresh({"rate_limits": {"five_hour": {"utilization": 37.0, "resets_at": "2026-10-08T14:00:00Z"},
                                       "seven_day": {"utilization": 12.0, "resets_at": "2026-10-12T00:00:00Z"}},
                       "behaviors": None, "subscription_type": "max"})
        usage = json.loads((Path(self.d) / "usage.json").read_text())
        self.assertEqual(((usage.get("five_hour") or {}).get("pct"), (usage.get("seven_day") or {}).get("pct")), (37, 12),
                         "the snapshot came from rate_limits alone: %r" % usage)


if __name__ == "__main__":
    unittest.main()
