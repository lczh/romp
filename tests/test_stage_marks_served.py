#!/usr/bin/env python3
"""T401 (5a), served: a REAL hermetic kernel over two synthetic sessions whose transcripts compacted, each with an assembly
document seeded before the boot through the writer every kernel path calls (em.asm_checkpoint_write, the settle's and the exit
drain's): the judge tiers' first pass builds through their per-session pools, a proto-2 chat client drives one real history
request over the socket, and /perf then reads NO `none` key in asmIndex.materializedByStage or asmCheckpoint.hydratedByStage, a
pool build under `judge.triage:<caller>`, and the socket request's build under `http.GET.ws:<caller>`. The request comes AFTER
the judge's pass and the push, the race a slower runner produced staged on purpose, and the kernel's resident index cap is tiny,
so the request rebuilds the rows it reads whoever built them first (2026-09-14: the lab had assumed the request was the first to
need them and read red in CI when it was not).
The documents are seeded, not left to a first kernel's exit drain (2026-10-08): the drain's assembly phase is a capped share of
the exit's deadline (0.275 s at the default grace since the exit-path change of 2026-10-05, 0.9 s before it) and walks the
listing newest first, so it wrote api's document and left web's unwritten whenever api's took longer than the cap. The kernel
then parsed web whole (no cut, render floor 0), the history request was answered from the list the push had built, and the
socket built and hydrated nothing of its own: the http.GET.ws pin read red in 6 of about 85 CI runs over the change's first two
days, and on every run on a slower box. Both documents are asserted on disk, by leaf, before the boot. Synthetic only: invented
text, placeholder uuids, TESTHOST. Counts only under ROMP_SERVED_TESTS_REQUIRE=1 (a served module never skips conditionally
there)."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from tests.dist_copy import copy_dist
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab                                  # noqa: E402  the lab kernel's environment
from test_fold_checkpoints_served import ChatClient, _free_port   # noqa: E402  the websocket client
from test_asm_checkpoint_served import transcript                # noqa: E402  the compacting synthetic transcript

# Hermetic state BEFORE the loads (tests/test_state_isolation_order.py): setUpClass load_source's the kernel to seed the
# documents, and romp code resolves its state root at import time
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
_ST0 = Path(os.environ["XDG_STATE_HOME"]) / "romp"
_ST0.mkdir(parents=True, exist_ok=True)
(_ST0 / "session-hosts").write_text("off\n")  # this module mints its own state root: hosts off (CLAUDE.md)

WEB = "aaaaaaaa-4444-4222-8333-444444444444"
API = "bbbbbbbb-4444-4222-8333-555555555555"


def _seed_documents(state, sessions, now):
    """Each session's assembly document, written into the lab's checkpoints directory by the kernel's own writer, as the
    cut-floor labs seed theirs (tests/test_federated_twocol_wall_served.py, _seed_cutfloor): a whole parse fills the leaf's
    assembly entry and em.asm_checkpoint_write writes the document from it. `sessions` is {sid: (name, leaf)}; returns
    {sid: (written, the document's path)}, the path as the writer names it for that leaf."""
    os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
    km = load_source("romp_kernel_stage_marks_seed", os.path.join(BIN, "romp-kernel"))
    jd, em = km.jd, km.em
    saved_state, saved = jd.STATE, (km._sessions, km._live_map)
    out = {}
    try:
        jd._rebind_state(Path(state))
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")
        km._live_map = lambda: {}
        for sid, (name, leaf) in sessions.items():
            row = {"sid": sid, "name": name, "path": leaf, "mtime": now, "anchor": sid}
            km._sessions = lambda now=None, _r=row, **kw: [_r]
            km.build_session(sid, now, {}, floor=0)        # the whole parse: the assembly entry the writer writes from
            ok = em.asm_checkpoint_write(leaf, sid, sdk_human=True, tree=km._parse(leaf, sid, now))   # sdk_human: an sdk session
            out[sid] = (bool(ok), str(em._asm_ckpt_file(leaf)))
    finally:
        km._sessions, km._live_map = saved
        em.set_checkpoint_dir(None)
        jd._rebind_state(saved_state)
    return out


class StageMarksOnAServedBoot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = tempfile.mkdtemp(prefix="romp-t401-5a-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        cls.dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), cls.dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cls.claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "goals", "timeline"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(cls.state, "session-hosts").write_text("off\n")       # the lab's state root pins the hosts off (the 2026-09-11 rule)
        proj = os.path.join(cls.claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 86400
        cls.leaves, names = {}, {}
        for sid, name, color in ((WEB, "web", "#9cd2ff"), (API, "api", "#ffd29c")):
            names[sid] = name
            Path(cls.state, "names", sid).write_text("%s\t%s\t%s\t#0c1a2e\n" % (name, cwd, color))
            Path(cls.state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5", "lastStopAt": t0 + 90}))
            Path(cls.state, "states", sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in (
                {"t": t0 + 10, "state": "working"}, {"t": t0 + 70, "state": "waiting"}, {"t": t0 + 71, "state": "idle"})))
            cls.leaves[sid] = os.path.join(proj, sid + ".jsonl")
            Path(cls.leaves[sid]).write_text("".join(json.dumps(r) + "\n" for r in transcript(t0, turns=900, compact_every=150)))
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.token = "testtok-t401-5a"
        cls.seeded = _seed_documents(cls.state, {sid: (names[sid], leaf) for sid, leaf in cls.leaves.items()}, int(time.time()))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.lab, ignore_errors=True)

    def _boot(self, index_cap=None):
        port = _free_port()
        seams = {"ROMP_HOST_NAME": "TESTHOST", "ROMP_PERF_STACKS": "1"}
        if index_cap is not None:
            seams["ROMP_ASM_INDEX_CAP"] = str(index_cap)   # a tiny resident cap: every reader rebuilds what an earlier one built and the LRU
        #                                                    dropped, so the socket request mints rows under ITS mark whoever built them first
        env = _lab.kernel_env(self.lab, self.claude, self.dist, port, self.token, **seams)
        logp = os.path.join(self.lab, "kernel-%d.log" % port)
        k = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(logp, "w"), stderr=subprocess.STDOUT, env=env)
        for _ in range(200):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            k.kill()
            raise unittest.SkipTest("hermetic kernel never served /healthz here")
        return k, port, logp

    def _get(self, port, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), headers={"X-Romp-Token": self.token})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    def _stop(self, k):
        k.send_signal(signal.SIGTERM)
        k.wait(timeout=30)

    def _log_tail(self, logp):
        with open(logp, errors="replace") as f:
            return "\n" + "\n".join(l.rstrip() for l in f if not l.startswith("reader:"))[-4000:]

    def _wait_for(self, port, pred, seconds, what):
        """Poll /perf until pred(perf) holds; the last perf on failure."""
        last = None
        deadline = time.time() + seconds
        while time.time() < deadline:                                 # loop-ok: bounded by `seconds`
            try:
                last = self._get(port, "/perf")
                if pred(last):
                    return last
            except Exception:
                pass
            time.sleep(1.0)
        self.fail("%s within %d s; last perf: asmIndex=%s judge=%s" % (what, seconds, (last or {}).get("asmIndex"), (last or {}).get("judge")))

    def _session_frame(self, client, log):
        client.send({"type": "ready", "proto": 2})                    # proto 2: uuid-keyed frames, the history asks
        for fr in client.frames(60):
            if fr.get("type") == "session" and (fr.get("id") == WEB or fr.get("sid") == WEB):
                return fr
        self.fail("no session frame for web within 60 s" + self._log_tail(log))

    def test_no_build_or_hydration_reads_none_and_the_pool_and_the_socket_land_under_their_marks(self):
        # Both documents on disk BEFORE the boot, each under its own leaf's name: web's is the one the history request reads
        # (a web parsed whole has render floor 0, and the request is then answered from the push's list with no build of its own)
        docs = sorted(f for f in os.listdir(os.path.join(self.state, "checkpoints")) if f.endswith(".asm.json.gz"))
        unwritten = {sid: self.seeded.get(sid) for sid in (WEB, API)
                     if sid not in self.seeded or not self.seeded[sid][0] or not os.path.exists(self.seeded[sid][1])}
        self.assertEqual(unwritten, {}, "each session's assembly document is on disk before the boot; written: %s" % docs)
        kern, port, log = self._boot(index_cap=200)
        try:
            # The judge's first pass and the pusher's cycle FIRST: the race staged on purpose. Round three's form sent the socket
            # request before the judge's pass (held BOOT_JUDGE_HOLD_S after boot) so the request would be the first to need the
            # documents' rows and mint them under http.GET.ws; on a slower runner the pass and the push had built and memoized them
            # before the request arrived, and the request minted nothing of its own (CI 2026-09-14: builds under judge.triage and
            # push only, none under http.GET.ws). With the pre-warm staged here and the resident cap tiny (index_cap above), the
            # rows the request reads were built by the pass and dropped by the LRU since, so the request rebuilds them under its own
            # mark deterministically; without the cap this staged order reads no http.GET.ws key (the red the race produced).
            self._wait_for(port, lambda pf: (pf.get("judge") or {}).get("passes", 0) >= 1
                           and any(k.startswith("judge.triage:") for k in ((pf.get("asmIndex") or {}).get("materializedByStage") or {})),
                           90, "the judge's first pass built through its pools under judge.triage")
            c = ChatClient(port, self.token, WEB)
            try:
                self._session_frame(c, log)
                c.send({"type": "loadAround", "id": WEB, "uuid": "u5"})    # one REAL history request over the socket
                for fr2 in c.frames(30):
                    if fr2.get("type") == "chatWindow":
                        self.assertFalse(fr2.get("missing"), "the anchor is a pre-cut turn's prompt: %s" % {k: fr2.get(k) for k in ("anchor", "missing")})
                        break
                else:
                    self.fail("no chatWindow reply within 30 s" + self._log_tail(log))
                perf = self._get(port, "/perf?stacks=1")                    # read while the socket is open: its handler thread is
                stages = {name: row.get("stage") for name, row in (perf.get("stacks") or {}).items()}   #  parked in the recv loop
                self.assertIn("http.GET.ws", stages.values(), "the socket's handler thread carries the request's mark for the "
                              "connection's life (the real request just answered ran under it): %s" % stages)
                by_ws = (perf.get("asmIndex") or {}).get("materializedByStage") or {}
                hy_ws = (perf.get("asmCheckpoint") or {}).get("hydratedByStage") or {}
                self.assertTrue(any(k.startswith("http.GET.ws:") for k in list(by_ws) + list(hy_ws)),
                                "the socket request's own builds or hydrations land under http.GET.ws: builds=%s hydrations=%s "
                                "(documents restored %s, turns %s)%s"
                                % (by_ws, hy_ws, (perf.get("asmCheckpoint") or {}).get("restored"),
                                   (perf.get("asmIndex") or {}).get("restoredTurns"), self._log_tail(log)))
            finally:
                c.close()
            perf = self._get(port, "/perf")
            by_stage = (perf.get("asmIndex") or {}).get("materializedByStage") or {}
            hy_stage = (perf.get("asmCheckpoint") or {}).get("hydratedByStage") or {}
            none_keys = sorted(k for k in list(by_stage) + list(hy_stage) if k.startswith("none:"))
            self.assertEqual(none_keys, [], "every build and hydration carries a thread's stage: builds=%s hydrations=%s%s"
                             % (by_stage, hy_stage, self._log_tail(log)))
            self.assertTrue(any(k.startswith("judge.triage:") for k in by_stage), "a pool build lands under judge.triage: %s" % by_stage)
            self.assertGreater(sum(by_stage.values()), 0)
        finally:
            self._stop(kern)


if __name__ == "__main__":
    unittest.main()
