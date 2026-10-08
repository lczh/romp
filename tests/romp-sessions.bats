#!/usr/bin/env bats

# `romp sessions [--json]`: the sessions' live state for scripts, read from the kernel.
#
# It exists because the only machine-readable source of per-session state and identity colour
# used to be the tmux session table (@claude-state, @identity-bg/@identity-fg) of the terminal
# backend removed on 2026-09-11. SDK sessions never entered tmux, so once they became the default
# `tmux list-sessions` returned nothing and every external consumer silently degraded to empty
# output. The kernel owns this state for BOTH backends (Claude Code and Codex), so that is what
# this reads, and a dead kernel must fail loudly rather than print an empty list, which would read
# as "no sessions" and hide exactly the breakage this command ends.

ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    TEST_DIR="$(mktemp -d)"
    export XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'TESTTOKEN123\n' > "$XDG_STATE_HOME/romp/serve-token"
    export ROMP_KERNEL_PORT=29855

    # Stub curl: records the request, replies with a two-session fleet. Nothing touches a real
    # kernel, so the suite is safe to run beside a live one.
    MOCK="$TEST_DIR/mock"; mkdir -p "$MOCK"
    export CURL_LOG="$TEST_DIR/curl.log"
    export FLEET_JSON="$TEST_DIR/fleet.json"
    cat > "$FLEET_JSON" <<'JSON'
{"sessions": [
  {"id": "11111111-2222-3333-4444-555555555555", "name": "web", "state": "working",
   "dir": "/tmp/notes-api", "bg": "#1EA1EB", "fg": "black", "backend": "sdk", "working": ""},
  {"id": "66666666-7777-8888-9999-000000000000", "name": "api", "state": "waiting",
   "dir": "/tmp/notes-api", "bg": "#54B204", "fg": "black", "backend": "codex", "working": ""}
]}
JSON
    export CURL_STDIN="$TEST_DIR/curl.stdin"
    # Record argv AND stdin: the serve token travels on stdin now (curl --config -), so a mock that
    # only watched argv could not tell a working auth header from no header at all. Read stdin
    # BEFORE writing the response, or the config never lands.
    # It honours -w the way curl does (the format after the body, the status filled in), so the script's status split
    # runs for real. CURL_FAIL: no reply at all (connection refused, curl's exit 7). CURL_CODE + CURL_BODY: a reply with
    # that status and body.
    cat > "$MOCK/curl" <<'MOCK'
#!/usr/bin/env bash
echo "$*" >> "$CURL_LOG"
cat >> "$CURL_STDIN" 2>/dev/null
[ -n "${CURL_FAIL:-}" ] && exit 7
_w=""
while [ $# -gt 0 ]; do [ "$1" = "-w" ] && _w="$2"; shift; done
if [ -n "${CURL_CODE:-}" ]; then printf '%s' "${CURL_BODY:-}"; else cat "$FLEET_JSON"; fi
if [ -n "$_w" ]; then printf '%b' "$(printf '%s' "$_w" | sed "s/%{http_code}/${CURL_CODE:-200}/")"; fi
exit 0
MOCK
    chmod +x "$MOCK/curl"
    export PATH="$MOCK:$PATH"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "romp sessions: prints a line per session with state and backend" {
    run "$ROMP_SCRIPT" sessions
    [ "$status" -eq 0 ]
    [[ "$output" == *"web"* ]]
    [[ "$output" == *"working"* ]]
    [[ "$output" == *"api"* ]]
    [[ "$output" == *"waiting"* ]]
}

@test "romp sessions: covers BOTH backends (Claude Code and Codex), the point of reading the kernel" {
    run "$ROMP_SCRIPT" sessions
    [ "$status" -eq 0 ]
    [[ "$output" == *"sdk"* ]]
    [[ "$output" == *"codex"* ]]
}

@test "romp sessions --json: emits the kernel's rows verbatim, colours included" {
    run "$ROMP_SCRIPT" sessions --json
    [ "$status" -eq 0 ]
    # the identity colours an external consumer used to read from @identity-bg
    [[ "$output" == *"#1EA1EB"* ]]
    [[ "$output" == *"#54B204"* ]]
    echo "$output" | python3 -c 'import json,sys; assert len(json.load(sys.stdin)["sessions"]) == 2'
}

@test "romp sessions: reads the KERNEL, authorizing with the serve token" {
    run "$ROMP_SCRIPT" sessions
    [ "$status" -eq 0 ]
    grep -q "127.0.0.1:29855/sessions" "$CURL_LOG"
    # The token goes in on stdin, never argv: /proc/<pid>/cmdline is world-readable, so a token in
    # argv hands full control of every session to any other account on the machine.
    grep -q "X-Romp-Token: TESTTOKEN123" "$CURL_STDIN"
    ! grep -q "TESTTOKEN123" "$CURL_LOG"
}

@test "romp sessions: a dead kernel fails LOUDLY, never an empty list read as no sessions" {
    CURL_FAIL=1 run "$ROMP_SCRIPT" sessions
    [ "$status" -ne 0 ]
    [[ "$output" == *"kernel not reachable"* ]]
}

@test "romp sessions: a kernel that answers its listing is unavailable says so, in the kernel's words, never not reachable" {
    # the kernel answers 503 while a build of its kept listing fails: it is running, and a consumer told "not reachable"
    # would go restart a healthy kernel
    CURL_CODE=503 CURL_BODY='{"ok": false, "error": "the session listing'"'"'s rows cannot be served: its build has failed since 10:00:00 (3 builds; the last: RuntimeError: the build failed), so the kept copy may be stale; the kernel log has the traceback"}' \
        run "$ROMP_SCRIPT" sessions
    [ "$status" -eq 1 ]
    [[ "$output" == *"the kernel answered, but its session listing is unavailable"* ]]
    [[ "$output" == *"RuntimeError: the build failed"* ]]
    [[ "$output" != *"not reachable"* ]]
}

@test "romp sessions: a refused token and an unexpected status are reported as what the kernel answered" {
    CURL_CODE=403 CURL_BODY='{"ok": false}' run "$ROMP_SCRIPT" sessions
    [ "$status" -eq 1 ]
    [[ "$output" == *"refused the serve token"* ]]
    CURL_CODE=500 CURL_BODY='oops' run "$ROMP_SCRIPT" sessions --json
    [ "$status" -eq 1 ]
    [[ "$output" == *"the kernel answered HTTP 500"* ]]
}

@test "romp color <session>: reads the colours off the listing, and a listing refusal is reported as the kernel's answer" {
    export ROMP_SERVE_TOKEN=TESTTOKEN123
    run "$ROMP_SCRIPT" color web
    [ "$status" -eq 0 ]
    [[ "$output" == *"#1EA1EB"* ]]
    CURL_CODE=503 CURL_BODY='{"ok": false, "error": "the session listing'"'"'s rows cannot be served: it has not been built; the kernel log has the traceback"}' \
        run "$ROMP_SCRIPT" color web
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp color: the kernel answered, but its session listing is unavailable"* ]]
    [[ "$output" != *"not reachable"* ]]
    CURL_FAIL=1 run "$ROMP_SCRIPT" color web
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel not reachable"* ]]
}

@test "romp sessions: an unknown flag is refused rather than silently ignored" {
    run "$ROMP_SCRIPT" sessions --nope
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp sessions"* ]]
}

@test "romp sessions: listed in help, under the scripting group" {
    run "$ROMP_SCRIPT" help
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp sessions"* ]]
}
