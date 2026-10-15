#!/usr/bin/env python3
"""The dashboard's sockets redial on a spread-out, backed-off schedule, and no socket state escapes the
watchdogs (2026-10-15).

Three gaps in the browser side of the connection, each run here against the REAL code under node:

- Every pane redialed on a fixed beat (1.5 s, or 250 ms around a restart or a return to the tab) and the
  shell's own socket on another (2 s), so all the panes of every open dashboard that one kernel restart dropped
  redialed in step, and kept doing so against a kernel that was slow to come back. Now each redial backs off
  (redialMs: an unannounced drop from 1 s doubling to 30 s, the two tight windows from 250 ms doubling to 2 s),
  is spread by up to a fifth either way at random, and starts over once an open socket receives a frame.
- The shell's socket had no watchdog at all: a handshake that hung waited out the browser's own timeout, and an
  OPEN socket whose far end died without a close reaching the browser never redialed. It keeps the panes' rules
  now.
- No watchdog handled a socket held CLOSING (a close the far side never completes, which the browser holds for up
  to a minute), and a WebSocket constructor that threw left the pane with no socket and the shell with no next
  dial. Both now always lead to the next dial.

The pane shim runs through km._shim_core_js() and the shell's socket block through km._shell_ws_js(), each at
module scope with fakes for Date.now, Math.random, WebSocket and the timers. Federation's relay sockets have
their CLOSING verdict in ui/webview/federation-reconnect.test.ts. Synthetic data only: TESTHOST as the host.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_wsredial", os.path.join(BIN, "romp-kernel"))

# What both blocks see of a browser. `var` at module scope shadows node's own timers for the code that follows in
# the same file. RND is the jitter's draw (0.5 = no spread, so a delay reads as its schedule's own value). A timer
# records when it is due (`at`); fireRedial() runs the newest pending redial at that time. THROW makes the next
# WebSocket constructor calls throw; a socket marked `stuck` stays CLOSING when closed, as one whose far side never
# completes the close does. tick() runs every interval inside a try, as a browser reports a throwing timer callback
# and keeps the interval running.
COMMON = r"""
var NOW=1000000;Date.now=function(){return NOW;};
var RND=0.5;Math.random=function(){return RND;};
var timers=[];var setTimeout=function(fn,ms){timers.push({fn:fn,ms:ms,at:NOW+ms,live:true});return timers.length;};
var clearTimeout=function(id){if(id&&timers[id-1])timers[id-1].live=false;};
var intervals=[],errors=[];var setInterval=function(fn,ms){intervals.push({fn:fn,ms:ms});return intervals.length;};
function tick(){for(var i=0;i<intervals.length;i++){try{intervals[i].fn();}catch(e){errors.push(String(e));}}}
var docL={};function fire(t){(docL[t]||[]).forEach(function(f){f({type:t});});}
var sockets=[],THROW=0;
function WebSocket(url){if(THROW>0){THROW--;throw new Error("refused");}this.url=url;this.readyState=0;this.sent=[];this.closed=0;this.stuck=false;sockets.push(this);}
WebSocket.prototype.send=function(s){this.sent.push(s);};
WebSocket.prototype.close=function(){this.closed++;this.readyState=this.stuck?2:3;};
function sock(){return sockets[sockets.length-1];}
function open(){var s=sock();s.readyState=1;s.onopen();return s;}
function recv(m){sock().onmessage({data:JSON.stringify(m)});}
function drop(){var s=sock();s.readyState=3;s.onclose({code:1006});}   // the current socket closes: an open one dropping, or a handshake failing
function pending(name){return timers.filter(function(t){return t.live&&t.fn.name===name;});}
function due(name){var p=pending(name);return p.length?p[p.length-1].ms:null;}
function fireRedial(name){var p=pending(name);var t=p[p.length-1];t.live=false;if(NOW<t.at)NOW=t.at;t.fn();}
function loseTimers(){timers.forEach(function(t){t.live=false;});}
function out(o){process.stdout.write(JSON.stringify(o));}
"""

PANE = COMMON + r"""
var document={visibilityState:"visible",wasDiscarded:false,addEventListener:function(t,f){(docL[t]=docL[t]||[]).push(f);},getElementById:function(){return null;}};
var parentPosts=[],winEvents=[],delivered=[];
var window={innerWidth:800,innerHeight:600,parent:{postMessage:function(m){parentPosts.push(m);}},
dispatchEvent:function(e){winEvents.push(e.type);return true;},sessionStorage:{getItem:function(){return "";}},
__rompFed:{inbound:function(h,m){delivered.push(m);}}};
var location={protocol:"http:",host:"TESTHOST",search:""};
var localStorage={getItem:function(){return null;},setItem:function(){}};
function MessageChannel(){var self=this;this.port1={onmessage:null};this.port2={postMessage:function(d){}};}
function rows(s,what){return s.sent.map(function(x){return JSON.parse(x);}).filter(function(m){return m.type==="clientDiag"&&(!what||m.what===what);});}
function hide(){document.visibilityState="hidden";fire("visibilitychange");}
function show(){document.visibilityState="visible";fire("visibilitychange");}
"""

SHELL = COMMON + r"""
var document={addEventListener:function(t,f){(docL[t]=docL[t]||[]).push(f);}};
var lostCalls=0,diagRows=[];var window={__rompApiSocketLost:function(){lostCalls++;}};var navigator={};
var location={protocol:"http:",host:"TESTHOST"};
var diagQ=[],shellSock=null;function shellDiag(what,data){diagRows.push({what:what,data:data});}
function diagStamp(m){return m;}function wid(){return "w1";}function reveal(){}
"""


def _node(src):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    fx = tempfile.mkdtemp()
    path = os.path.join(fx, "run.js")
    with open(path, "w") as f:
        f.write(src)
    r = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError("node failed:\n" + r.stderr)
    return json.loads(r.stdout)


def _pane(scenario, pre=""):
    return _node(PANE + pre + km._shim_core_js("test") + "\n" + scenario)


def _shell(scenario, pre=""):
    return _node(SHELL + pre + km._shell_ws_js() + "\n" + scenario)


class PaneBackoff(unittest.TestCase):
    """The pane shim's redial: doubling, capped, jittered, and reset by a frame."""

    def test_an_unannounced_outage_backs_off_from_1_s_doubling_to_a_30_s_cap(self):
        r = _pane(r"""
open();recv({type:"ka"});drop();var ds=[due("connect")];
for(var i=0;i<7;i++){fireRedial("connect");drop();ds.push(due("connect"));}
out({ds:ds,live:pending("connect").length});""")
        self.assertEqual(r["ds"], [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000],
                         "each dial in a row that went unanswered doubles the next wait, up to 30 s")
        self.assertEqual(r["live"], 1, "one redial pending at a time")

    def test_the_spread_is_a_fifth_either_way_so_sockets_dropped_together_redial_apart(self):
        r = _pane(r"""
open();recv({type:"ka"});RND=0;drop();var lo=due("connect");fireRedial("connect");
RND=0.999999;drop();var hi=due("connect");fireRedial("connect");
RND=0.25;drop();var mid=due("connect");
out({lo:lo,hi:hi,mid:mid});""")
        self.assertEqual(r["lo"], 800, "the lowest draw: 1 s less a fifth")
        self.assertEqual(r["hi"], 2400, "the highest draw on the second step: 2 s plus a fifth")
        self.assertEqual(r["mid"], 3600, "a draw of a quarter: 4 s less a tenth")

    def test_a_frame_on_an_open_socket_starts_the_schedule_over_and_an_open_alone_does_not(self):
        r = _pane(r"""
open();recv({type:"ka"});drop();fireRedial("connect");drop();var second=due("connect");fireRedial("connect");
open();drop();var afterSilentOpen=due("connect");fireRedial("connect");
open();recv({type:"ka"});drop();var afterFrame=due("connect");
out({second:second,afterSilentOpen:afterSilentOpen,afterFrame:afterFrame});""")
        self.assertEqual(r["second"], 2000)
        self.assertEqual(r["afterSilentOpen"], 4000, "a socket that opened and heard nothing is no evidence the kernel is back")
        self.assertEqual(r["afterFrame"], 1000, "a socket that opened and received starts the schedule over")

    def test_an_announced_restart_backs_off_from_250_ms_to_2_s_then_the_unannounced_schedule_starts_at_its_floor(self):
        r = _pane(r"""
open();recv({type:"ka"});recv({type:"restarting"});var annT=NOW;drop();var ds=[due("connect")];
for(var i=0;i<4;i++){fireRedial("connect");drop();ds.push(due("connect"));}
fireRedial("connect");NOW=annT+30001;drop();var after=[due("connect")];fireRedial("connect");drop();after.push(due("connect"));
out({ds:ds,after:after});""")
        self.assertEqual(r["ds"], [250, 500, 1000, 2000, 2000], "an announced death keeps a tight redial, doubling to 2 s")
        self.assertEqual(r["after"], [1000, 2000],
                         "a restart that outlasts its announcement goes on from the unannounced schedule's 1 s, not from where the tight one left off")

    def test_a_return_to_the_tab_redials_the_first_close_now_and_backs_off_the_rest_from_250_ms(self):
        r = _pane(r"""
open();recv({type:"ka"});hide();NOW+=5000;show();drop();var ds=[due("connect")];
for(var i=0;i<4;i++){fireRedial("connect");drop();ds.push(due("connect"));}
out({ds:ds});""")
        self.assertEqual(r["ds"], [0, 250, 500, 1000, 2000], "the close the return thawed into redials at once (2026-09-07); the next ones back off")

    def test_a_lost_timer_is_judged_against_the_redial_s_due_time_so_a_long_backoff_is_not_cut_short(self):
        r = _pane(r"""
open();recv({type:"ka"});drop();for(var i=0;i<4;i++){fireRedial("connect");drop();}
var wait=due("connect"),n0=sockets.length;
NOW+=9000;tick();var at9=sockets.length;
loseTimers();NOW+=wait-9000+8001;tick();var lost=sockets.length;
out({wait:wait,n0:n0,at9:at9,lost:lost});""")
        self.assertEqual(r["wait"], 16000)
        self.assertEqual(r["at9"], r["n0"], "9 s into a 16 s wait the watchdog leaves the scheduled redial alone")
        self.assertEqual(r["lost"], r["n0"] + 1, "8 s past the redial's due time with no dial, the timer was lost: the watchdog dials")


class PaneClosingAndRefusedDials(unittest.TestCase):
    """A socket held CLOSING and a constructor that throws both lead to the next dial."""

    def test_a_socket_held_closing_is_abandoned_on_the_tick_after_the_one_that_first_saw_it(self):
        r = _pane(r"""
open();recv({type:"ka"});var s=sock();s.stuck=true;s.readyState=2;
NOW+=5000;tick();var first=sockets.length;
NOW+=5000;tick();var second=sockets.length;var detached=s.onclose===null;
open();
out({first:first,second:second,detached:detached,wsdown:winEvents.filter(function(e){return e==="romp:wsdown";}).length,
row:rows(sock(),"watchdog-close").map(function(m){return m.data.why;})});""")
        self.assertEqual(r["first"], 1, "the first tick that sees it CLOSING only stamps it")
        self.assertEqual(r["second"], 2, "the next tick puts it down and dials, rather than waiting out the browser's close")
        self.assertTrue(r["detached"], "its handlers are detached: its late onclose redials nothing")
        self.assertEqual(r["wsdown"], 1, "the abandon says the socket is down, as its onclose would have")
        self.assertEqual(r["row"], ["closing"], "a watchdog-close row naming the state rides the redial")

    def test_a_handshake_aborted_into_a_stuck_close_is_abandoned_and_counted_as_a_failed_dial(self):
        r = _pane(r"""
open();recv({type:"ka"});drop();fireRedial("connect");var s=sock();s.stuck=true;
NOW+=15001;tick();var aborted=s.readyState;NOW+=5000;tick();var stamped=sockets.length;NOW+=5000;tick();
open();var cf=rows(sock(),"wsconnfail").map(function(m){return m.data.attempts;});
out({aborted:aborted,stamped:stamped,after:sockets.length,cf:cf});""")
        self.assertEqual(r["aborted"], 2, "premise: the 15 s abort left the handshake CLOSING")
        self.assertEqual(r["stamped"], 2)
        self.assertEqual(r["after"], 3, "abandoned and redialed")
        self.assertEqual(r["cf"], [1], "it never opened, so the next open's wsconnfail row counts it")

    def test_a_return_to_the_tab_abandons_a_socket_held_closing_at_once(self):
        r = _pane(r"""
open();recv({type:"ka"});hide();NOW+=5000;var s=sock();s.stuck=true;s.readyState=2;show();
out({sockets:sockets.length,detached:s.onclose===null});""")
        self.assertEqual(r["sockets"], 2, "the return redials now, as it does for a quiet OPEN socket")
        self.assertTrue(r["detached"])

    def test_a_constructor_that_throws_after_an_abandon_schedules_the_next_dial(self):
        r = _pane(r"""
open();recv({type:"ka"});NOW+=30001;THROW=1;tick();
var after={errors:errors.length,sockets:sockets.length,ws:ws===null,due:due("connect")};
fireRedial("connect");var redialed=sockets.length;open();
out({after:after,redialed:redialed,cf:rows(sock(),"wsconnfail").map(function(m){return m.data.attempts;})});""")
        a = r["after"]
        self.assertEqual(a["errors"], 0, "the throw is caught inside connect(), not left to escape the watchdog")
        self.assertEqual(a["sockets"], 1)
        self.assertTrue(a["ws"], "no socket is held for the refused dial")
        self.assertEqual(a["due"], 1000, "the next dial is scheduled on the unannounced schedule")
        self.assertEqual(r["redialed"], 2, "and it dials")
        self.assertEqual(r["cf"], [1], "the refused dial is counted in the next open's wsconnfail row")

    def test_a_refused_dial_whose_timer_is_lost_is_still_redialed_by_the_watchdog(self):
        r = _pane(r"""
open();recv({type:"ka"});NOW+=30001;THROW=1;tick();loseTimers();
NOW+=5000;tick();var early=sockets.length;NOW+=5000;tick();
out({early:early,late:sockets.length});""")
        self.assertEqual(r["early"], 1, "inside 8 s of the redial's due time the watchdog waits for the timer")
        self.assertEqual(r["late"], 2, "a missing socket with no live timer is redialed like a CLOSED one")


class ShellSocket(unittest.TestCase):
    """The shell page's own socket: the panes' schedule and the panes' watchdog."""

    def test_the_block_is_anchored_once_and_carries_the_shared_schedule(self):
        core = km._shell_ws_js()
        self.assertIn("function shellWS(){", core)
        self.assertIn(km._REDIAL_JS, core, "the shell's block takes the shared redialMs")
        self.assertIn(km._REDIAL_JS, km._shim("chat"), "the pane shim's written-out copy is the same text, character for character")
        self.assertNotIn("setTimeout(shellWS,2000)", km._LANDING_MOBILE_JS, "the fixed 2 s redial is gone")
        self.assertEqual(km._landing().count("/*shell-ws*/"), 1)
        saved = km._LANDING_MOBILE_JS
        try:
            km._LANDING_MOBILE_JS = saved.replace("/*end-shell-ws*/", "")
            with self.assertRaises(RuntimeError):
                km._shell_ws_js()
        finally:
            km._LANDING_MOBILE_JS = saved

    def test_a_close_backs_off_from_1_s_doubling_to_30_s_and_a_frame_starts_it_over(self):
        r = _shell(r"""
open();recv({type:"ka"});drop();var ds=[due("shellWS")];
for(var i=0;i<6;i++){fireRedial("shellWS");drop();ds.push(due("shellWS"));}
fireRedial("shellWS");open();recv({type:"ka"});drop();var reset=due("shellWS");
RND=0;fireRedial("shellWS");open();recv({type:"ka"});drop();var lo=due("shellWS");
out({ds:ds,reset:reset,lo:lo,lost:lostCalls});""")
        self.assertEqual(r["ds"], [1000, 2000, 4000, 8000, 16000, 30000, 30000])
        self.assertEqual(r["reset"], 1000, "a frame on an open socket starts the schedule over")
        self.assertEqual(r["lo"], 800, "spread at random, a fifth either way")
        self.assertEqual(r["lost"], 9, "every close tells the API health detail before the redial")

    def test_an_announced_restart_redials_from_250_ms_doubling_to_2_s(self):
        r = _shell(r"""
open();recv({type:"restarting"});var annT=NOW;drop();var ds=[due("shellWS")];
for(var i=0;i<4;i++){fireRedial("shellWS");drop();ds.push(due("shellWS"));}
fireRedial("shellWS");NOW=annT+30001;drop();var after=due("shellWS");
out({ds:ds,after:after});""")
        self.assertEqual(r["ds"], [250, 500, 1000, 2000, 2000])
        self.assertEqual(r["after"], 1000, "past the announcement's 30 s the unannounced schedule starts at its floor")

    def test_an_open_socket_silent_past_30_s_is_abandoned_and_redialed_at_once(self):
        r = _shell(r"""
var s=open();recv({type:"ka"});NOW+=30000;tick();var at30=sockets.length;
NOW+=1;tick();
out({at30:at30,after:sockets.length,detached:s.onclose===null,closed:s.closed,lost:lostCalls,sock:shellSock===null,rows:diagRows});""")
        self.assertEqual(r["at30"], 1, "a keepalive is due every 10 s: 30 s of silence is the bound, not past it")
        self.assertEqual(r["after"], 2, "silent past the bound: a fresh socket is dialed in the same tick")
        self.assertTrue(r["detached"], "the dead socket's handlers are detached, so its late onclose redials nothing")
        self.assertEqual(r["closed"], 1)
        self.assertEqual(r["lost"], 1, "the API health detail is told, as a close tells it")
        self.assertTrue(r["sock"], "the shell's send no longer writes into the dead socket")
        self.assertEqual(r["rows"], [{"what": "watchdog-close", "data": {"why": "quiet", "ms": 30001}}])

    def test_a_handshake_pending_past_15_s_is_aborted_and_its_close_redials_on_the_schedule(self):
        r = _shell(r"""
var s=sock();NOW+=15000;tick();var at15=s.closed;NOW+=1;tick();var aborted=s.closed;
s.onclose({code:1006});
out({at15:at15,aborted:aborted,due:due("shellWS"),rows:diagRows.map(function(x){return x.data.why;})});""")
        self.assertEqual(r["at15"], 0)
        self.assertEqual(r["aborted"], 1, "a handshake still pending past 15 s is aborted")
        self.assertEqual(r["due"], 1000, "its close redials on the schedule")
        self.assertEqual(r["rows"], ["connecting"])

    def test_a_socket_held_closing_is_abandoned_on_the_tick_after_the_one_that_first_saw_it(self):
        r = _shell(r"""
var s=open();recv({type:"ka"});s.stuck=true;s.readyState=2;NOW+=5000;tick();var first=sockets.length;
NOW+=5000;tick();
out({first:first,after:sockets.length,detached:s.onclose===null,rows:diagRows.map(function(x){return x.data.why;})});""")
        self.assertEqual(r["first"], 1)
        self.assertEqual(r["after"], 2)
        self.assertTrue(r["detached"])
        self.assertEqual(r["rows"], ["closing"])

    def test_a_constructor_that_throws_schedules_the_next_dial_even_at_load(self):
        r = _shell(r"""
var atLoad={sockets:sockets.length,due:due("shellWS"),errors:errors.length};
fireRedial("shellWS");var redialed=sockets.length;
open();recv({type:"ka"});THROW=1;drop();fireRedial("shellWS");var again={sockets:sockets.length,due:due("shellWS")};
out({atLoad:atLoad,redialed:redialed,again:again});""", pre="THROW=1;")
        self.assertEqual(r["atLoad"], {"sockets": 0, "due": 1000, "errors": 0},
                         "the page's first dial was refused: the next one is scheduled, where the throw used to end the shell's dialing")
        self.assertEqual(r["redialed"], 1)
        self.assertEqual(r["again"], {"sockets": 1, "due": 2000}, "a refused redial schedules the next, backed off")

    def test_a_lost_timer_is_redialed_8_s_past_its_due_time_and_a_long_backoff_is_not_cut_short(self):
        r = _shell(r"""
open();recv({type:"ka"});drop();for(var i=0;i<4;i++){fireRedial("shellWS");drop();}
var wait=due("shellWS"),n0=sockets.length;NOW+=9000;tick();var at9=sockets.length;
loseTimers();NOW+=wait-9000+8001;tick();
out({wait:wait,n0:n0,at9:at9,after:sockets.length});""")
        self.assertEqual(r["wait"], 16000)
        self.assertEqual(r["at9"], r["n0"])
        self.assertEqual(r["after"], r["n0"] + 1)

    def test_one_live_attempt_at_a_time(self):
        r = _shell(r"""
shellWS();var connecting=sockets.length;open();shellWS();
out({connecting:connecting,open:sockets.length});""")
        self.assertEqual((r["connecting"], r["open"]), (1, 1), "a call while a socket is connecting or open dials nothing")

    def test_a_frozen_tab_s_resume_re_bases_the_silence_and_a_socket_silent_after_the_thaw_is_still_put_down(self):
        r = _shell(r"""
open();recv({type:"ka"});NOW+=45000;fire("resume");tick();var kept=sockets.length;
NOW+=30001;tick();
out({kept:kept,after:sockets.length});""")
        self.assertEqual(r["kept"], 1, "the frozen stretch ran no script: it is not silence")
        self.assertEqual(r["after"], 2, "silence after the thaw is real, and the watchdog puts the socket down")


if __name__ == "__main__":
    unittest.main()
