#!/usr/bin/env bats

# `romp new --speed standard|fast <name>` (the user 2026-11-15, who wanted a session's speed chosen where it is
# created): the flag rides POST /new as "speed", the kernel writes it as the session's own fast-mode pick before the
# first launch (tests/test_new_session_speed.py), and this script says what the kernel answered. A value outside the
# two, and a Codex spawn, are refused HERE, before any request. Synthetic names and ids only; the kernel is a one-shot
# fake that records the body and answers what the test hands it.

ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    TEST_DIR="$(mktemp -d)"
    WORK_DIR="$TEST_DIR/notes-api"
    export HOME="$TEST_DIR/home"
    export XDG_STATE_HOME="$HOME/.local/state"
    export XDG_CONFIG_HOME="$HOME/.config"
    unset ROMP_STATE_DIR      # a profiled kernel's export outranks the XDG floor
    unset ROMP_SID            # outside a romp session: `romp new` names no parent
    # dead ports, so a test that starts no fake kernel can never reach the machine's own
    export ROMP_MANAGER_PORT=1 ROMP_SERVE_PORT=1 ROMP_KERNEL_PORT=1
    mkdir -p "$HOME" "$WORK_DIR" "$XDG_STATE_HOME/romp"
    printf 'tok-test' > "$XDG_STATE_HOME/romp/serve-token"
    cd "$WORK_DIR"
}

teardown() {
    [[ -n "${KERNEL_PID:-}" ]] && kill "$KERNEL_PID" 2>/dev/null
    rm -rf "$TEST_DIR"
}

# A one-shot fake kernel: records the /new body in $TEST_DIR/req.log and answers the JSON object in $1, with the
# body's "speed" echoed back as `speed` when $2 is "echo" (the applied-ack contract of the real handler).
_fake_kernel() {
    python3 - "$TEST_DIR/port" "$TEST_DIR/req.log" "$1" "${2:-}" <<'PY' &
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
portfile, log, answer, echo = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), sys.argv[4]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with open(log, "w") as f:
            json.dump({"path": self.path, "body": body}, f)
        out = dict(answer)
        if echo == "echo" and body.get("speed"):
            out["speed"] = body["speed"]
        out = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a): pass
class _Bound(HTTPServer):   # no reverse lookup of the bind address (socket.getfqdn takes about 36 s on GitHub's macOS images)
    def server_bind(self):
        import socketserver
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
srv = _Bound(("127.0.0.1", 0), H)
with open(portfile, "w") as f:
    f.write(str(srv.server_address[1]))
srv.handle_request()
PY
    KERNEL_PID=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
    export ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")"
}

@test "new --speed: rides /new as speed and the applied line names it" {
    _fake_kernel '{"ok": true, "id": "11111111-2222-3333-4444-555555555555"}' echo
    run "$ROMP_SCRIPT" new --speed standard web
    [ "$status" -eq 0 ]
    grep -q '"speed": "standard"' "$TEST_DIR/req.log"
    [[ "$output" == *"started \"web\""* ]]
    [[ "$output" == *"romp new: applied speed standard"* ]]
    [[ "$output" != *"refused"* ]]
    [[ "$output" != *"did not acknowledge"* ]]
}

@test "new --speed fast beside --effort: both ride, both are reported" {
    _fake_kernel '{"ok": true, "id": "11111111-2222-3333-4444-555555555555", "effort": "high"}' echo
    run "$ROMP_SCRIPT" new --effort high --speed fast web
    [ "$status" -eq 0 ]
    grep -q '"speed": "fast"' "$TEST_DIR/req.log"
    grep -q '"effort": "high"' "$TEST_DIR/req.log"
    [[ "$output" == *"romp new: applied effort high, speed fast"* ]]
}

@test "new without --speed sends no speed, and --in and --env still split cleanly behind it" {
    _fake_kernel '{"ok": true, "id": "11111111-2222-3333-4444-555555555555", "tags": ["research"], "tagsRequested": ["research"], "tagsApplied": ["research"], "env": {"FEATURE_FLAG": "1"}}'
    run "$ROMP_SCRIPT" new --in research --env FEATURE_FLAG=1 web
    [ "$status" -eq 0 ]
    [[ "$output" != *"speed"* ]]
    grep -q '"tags": \["research"\]' "$TEST_DIR/req.log"
    grep -q '"env": {"FEATURE_FLAG": "1"}' "$TEST_DIR/req.log"
    [ "$(grep -c '"speed"' "$TEST_DIR/req.log")" -eq 0 ]
}

@test "new --speed with --in and --env: the three keep their own values" {
    _fake_kernel '{"ok": true, "id": "11111111-2222-3333-4444-555555555555", "tags": ["research"], "tagsRequested": ["research"], "tagsApplied": ["research"], "env": {"FEATURE_FLAG": "1"}}' echo
    run "$ROMP_SCRIPT" new --speed standard --in research --env FEATURE_FLAG=1 web
    [ "$status" -eq 0 ]
    grep -q '"speed": "standard"' "$TEST_DIR/req.log"
    grep -q '"tags": \["research"\]' "$TEST_DIR/req.log"
    grep -q '"env": {"FEATURE_FLAG": "1"}' "$TEST_DIR/req.log"
    [[ "$output" == *"applied speed standard, env FEATURE_FLAG=1, tags research"* ]]
}

@test "new --speed: a word that is no speed is refused before any request, and nothing is created" {
    run "$ROMP_SCRIPT" new --speed turbo web
    [ "$status" -eq 2 ]
    [[ "$output" == *"romp new: --speed expects standard or fast (got: turbo)"* ]]
    [ ! -e "$TEST_DIR/req.log" ]
    # a missing value, and a flag where the value belongs, are the usage line, which names the flag
    run "$ROMP_SCRIPT" new web --speed
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp new"* ]]
    [[ "$output" == *"[--speed standard|fast]"* ]]
    run "$ROMP_SCRIPT" new --speed --codex web
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp new"* ]]
}

@test "new --speed on a Codex spawn is refused by name, by the flag or by the machine's engine default" {
    run "$ROMP_SCRIPT" new --codex --speed standard web
    [ "$status" -eq 2 ]
    [[ "$output" == *"romp new: --speed needs an SDK session, and --codex makes this a Codex one"* ]]
    [[ "$output" == *"a Codex session has no fast mode"* ]]
    printf 'codex' > "$XDG_STATE_HOME/romp/default-backend"
    run "$ROMP_SCRIPT" new --speed fast web
    [ "$status" -eq 2 ]
    [[ "$output" == *"romp engine codex"* ]]
    [ ! -e "$TEST_DIR/req.log" ]
}

@test "new --speed on a name that already runs: the kernel's refusal is said with its reason, not as a dropped ask" {
    _fake_kernel '{"ok": true, "id": "11111111-2222-3333-4444-555555555555", "existing": true, "refused": "the reason the kernel gave"}'
    local _out _st=0
    _out="$("$ROMP_SCRIPT" new --speed standard web 2>"$TEST_DIR/err")" || _st=$?
    [ "$_st" -eq 0 ]
    [[ "$_out" == *"\"web\" is already running"* ]]
    [ "$(grep -c 'refused' "$TEST_DIR/err")" -eq 1 ]
    grep -q 'romp new: speed standard refused: the reason the kernel gave' "$TEST_DIR/err"
    [[ "$_out" != *"applied"* ]]
    # the kernel ANSWERED the ask: no older-kernel warning on either stream
    [ "$(grep -c 'did not acknowledge' "$TEST_DIR/err")" -eq 0 ]
    [[ "$_out" != *"did not acknowledge"* ]]
}

@test "new --speed: a kernel that does not echo it warns loudly and names the Fast badge" {
    _fake_kernel '{"ok": true, "id": "11111111-2222-3333-4444-555555555555"}'
    run "$ROMP_SCRIPT" new --speed standard web
    [ "$status" -eq 0 ]
    [[ "$output" == *"did not acknowledge --speed (older kernel?)"* ]]
    [[ "$output" == *"Fast badge"* ]]
}

@test "new: help names --speed with its two values" {
    run "$ROMP_SCRIPT" -h
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp new --speed <standard|fast> <name>"* ]]
    [[ "$output" == *"Always fast"* ]]
}
