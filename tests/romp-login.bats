#!/usr/bin/env bats

# `romp login add | list | remove | link | unlink` (T346; docs/reference.md "Several Claude logins"): the CLI door to the stored
# Claude logins. Run against a mock curl (the kernel's /logins answers), recording its calls: `add` hands the kernel
# a label and the COMMAND that prints the token, never a token and no store's shorthand (the user 2026-09-13); `list`
# prints labels only. The mock answers as real curl does: with -w it appends the status after the body, and with -f a
# status of 400 or more exits 22 and prints nothing. MOCK_CURL_STATUS and MOCK_CURL_BODY make a POST answer with that
# status and body; MOCK_CURL_DOWN=1 is a kernel that never answers (curl's exit 7, connection refused).

ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    TEST_DIR="$(mktemp -d)"
    MOCK_DIR="$TEST_DIR/mock"
    export MOCK_LOG="$TEST_DIR/mock.log"
    mkdir -p "$MOCK_DIR"
    export HOME="$TEST_DIR/home"
    export XDG_STATE_HOME="$HOME/.local/state"
    mkdir -p "$HOME"
    unset ROMP_STATE_DIR
    export ROMP_MANAGER_PORT=1 ROMP_SERVE_PORT=1 ROMP_KERNEL_PORT=1
    export ROMP_SERVE_TOKEN=testtok
    cat > "$MOCK_DIR/curl" << 'MOCK'
#!/usr/bin/env bash
echo "curl $*" >> "$MOCK_LOG"
[[ " $* " == *" --config - "* ]] && cat >/dev/null
url=""; body=""; prev=""; wfmt=""; fail=0
for a in "$@"; do
  [[ "$a" == http* ]] && url="$a"; [[ "$prev" == "-d" ]] && body="$a"; [[ "$prev" == "-w" ]] && wfmt="$a"
  [[ "$a" == "-f" || "$a" == -[!-]*f* ]] && fail=1
  prev="$a"
done
reply() {   # reply <status> <body>: what real curl prints for that answer, and its exit
  if [[ $fail -eq 1 && "$1" -ge 400 ]]; then exit 22; fi
  if [[ -n "$wfmt" ]]; then printf '%s%b' "$2" "${wfmt//\%\{http_code\}/$1}"; else printf '%s\n' "$2"; fi
  exit 0
}
if [[ -n "${MOCK_CURL_DOWN:-}" ]]; then [[ -n "$wfmt" ]] && printf '%b' "${wfmt//\%\{http_code\}/000}"; exit 7; fi
if [[ "$url" == */logins && -n "$body" ]]; then
  echo "body $body" >> "$MOCK_LOG"
  [[ -n "${MOCK_CURL_STATUS:-}" ]] && reply "$MOCK_CURL_STATUS" "${MOCK_CURL_BODY:-}"
  if [[ "$body" == *'"link"'* ]]; then
    if [[ "$body" == *'"NOHOST"'* ]]; then reply 200 '{"ok": false, "error": "no connected kernel is named '"'"'NOHOST'"'"' (this machine is TESTHOST; connected: PEERHOST)"}'
    elif [[ "$body" == *'"on": "PEERHOST"'* ]]; then reply 200 '{"ok": true, "id": "0123456789ab", "label": "Work", "linked": {"host": "TESTHOST", "at": 1700000000}, "account": "dev@example.com · Example Org · enterprise", "on": "PEERHOST"}'
    else reply 200 '{"ok": true, "id": "0123456789ab", "label": "Work", "linked": {"host": "PEERHOST", "at": 1700000000}, "account": "dev@example.com · Example Org · enterprise"}'; fi
  fi
  if [[ "$body" == *'"unlink"'* ]]; then
    if [[ "$body" == *'"Old"'* ]]; then reply 200 '{"ok": false, "error": "the Old login is not linked to any sign-in"}'; else reply 200 '{"ok": true, "id": "0123456789ab", "label": "Work", "unlinked": true}'; fi
  fi
  if [[ "$body" == *'"remove"'* ]]; then
    if [[ "$body" == *'"Nope"'* ]]; then reply 200 '{"ok": false, "error": "no stored login named '"'"'Nope'"'"'"}'; else reply 200 '{"ok": true, "removed": "0123456789ab", "label": "Work"}'; fi
  fi
  reply 200 '{"ok": true, "id": "0123456789ab", "label": "Work", "display": "Work"}'
fi
if [[ "$url" == */logins ]]; then
  reply 200 '{"ok": true, "machine": {"label": "user@example.com · personal"}, "logins": [{"id": "0123456789ab", "label": "Work", "display": "Work · Acme · enterprise", "addedAt": 1700000000, "hasCmd": true, "why": "", "expiresSoon": false, "linked": {"host": "PEERHOST", "at": 1700000000}}, {"id": "abcdefabcdef", "label": "Old", "display": "Old", "addedAt": 1600000000, "hasCmd": true, "why": "the Old login was refused: expired", "expiresSoon": false}]}'
fi
reply 200 '{"ok": true}'
MOCK
    chmod +x "$MOCK_DIR/curl"
    export PATH="$MOCK_DIR:$PATH"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "romp help lists the verb" {
    run "$ROMP_SCRIPT" help
    [[ "$output" == *"romp login add|list|remove|link|unlink <label>"* ]]
}

@test "login list prints the machine login and the stored labels with their state, never a command" {
    run "$ROMP_SCRIPT" login list
    [ "$status" -eq 0 ]
    [[ "$output" == *"machine login: user@example.com · personal"* ]]
    [[ "$output" == *"Work · Acme · enterprise  (id 0123456789ab, added 2023-11-1"*"; linked by you to PEERHOST's sign-in)"* ]]
    [[ "$output" == *"Old  (id abcdefabcdef"*"the Old login was refused: expired)"* ]]
    [[ "$output" != *"op read"* ]]
    grep -q 'curl .*/logins' "$MOCK_LOG"
}

@test "login add --cmd records the command verbatim and nothing else" {
    run "$ROMP_SCRIPT" login add Work --cmd 'cat ~/.secrets/enterprise-token'
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp login: added 'Work'"* ]]
    grep -q '"add": {"label": "Work", "tokenCmd": "cat ~/.secrets/enterprise-token"}' "$MOCK_LOG"
}

@test "login add takes --cmd only: a store's shorthand is a usage error, and no command is a usage error" {
    run "$ROMP_SCRIPT" login add Work --op 'op://Vault/item/credential'
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp login add <label> --cmd"* ]]
    [ "$(cat "$MOCK_LOG" 2>/dev/null | grep -c opRef)" -eq 0 ]   # a count over the log (absent when the call is refused before any request), never a bare negation before another command (the bats ratchet)
    run "$ROMP_SCRIPT" login add Work
    [ "$status" -eq 2 ]
    [[ "$output" == *"--cmd '<shell line that prints the token>' is required"* ]]
}

@test "login remove relays the kernel's answer; usage errors exit 2" {
    run "$ROMP_SCRIPT" login remove Work
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp login: removed Work"* ]]
    grep -q '"remove": "Work"' "$MOCK_LOG"
    run "$ROMP_SCRIPT" login remove Nope
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: refused: no stored login named"* ]]
    run "$ROMP_SCRIPT" login
    [ "$status" -eq 2 ]
    run "$ROMP_SCRIPT" login remove
    [ "$status" -eq 2 ]
    run "$ROMP_SCRIPT" login list extra
    [ "$status" -eq 2 ]
}

@test "login link --same-as asks the kernel to copy that machine's sign-in and says the link is the user's" {
    run "$ROMP_SCRIPT" login link Work --same-as PEERHOST
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp login: linked 'Work' to PEERHOST's sign-in (dev@example.com · Example Org · enterprise)"* ]]
    [[ "$output" == *"your statement"* ]]
    grep -q '"link": {"login": "Work", "sameAs": "PEERHOST"}' "$MOCK_LOG"
}

@test "login link relays the kernel's refusal; a missing --same-as or login is a usage error" {
    run "$ROMP_SCRIPT" login link Work --same-as NOHOST
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: refused: no connected kernel is named 'NOHOST'"* ]]
    run "$ROMP_SCRIPT" login link Work
    [ "$status" -eq 2 ]
    [[ "$output" == *"--same-as <host> is required"* ]]
    run "$ROMP_SCRIPT" login link --same-as PEERHOST
    [ "$status" -eq 2 ]
    run "$ROMP_SCRIPT" login link Work --same-as
    [ "$status" -eq 2 ]
}

@test "login link --on asks this kernel to record the link on the connected kernel that holds the login" {
    run "$ROMP_SCRIPT" login link Work --on PEERHOST --same-as local
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp login: linked 'Work' on PEERHOST to TESTHOST's sign-in"* ]]
    grep -q '"link": {"login": "Work", "sameAs": "local"}, "on": "PEERHOST"' "$MOCK_LOG"
    run "$ROMP_SCRIPT" login unlink Work --on PEERHOST
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp login: unlinked Work on PEERHOST"* ]]
    grep -q '"unlink": "Work", "on": "PEERHOST"' "$MOCK_LOG"
    run "$ROMP_SCRIPT" login link Work --same-as local --on
    [ "$status" -eq 2 ]
    run "$ROMP_SCRIPT" login unlink Work Other
    [ "$status" -eq 2 ]
}

@test "login unlink takes the link back and relays a refusal" {
    run "$ROMP_SCRIPT" login unlink Work
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp login: unlinked Work"* ]]
    grep -q '"unlink": "Work"' "$MOCK_LOG"
    run "$ROMP_SCRIPT" login unlink Old
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: refused: the Old login is not linked to any sign-in"* ]]
    run "$ROMP_SCRIPT" login unlink
    [ "$status" -eq 2 ]
}

@test "without a serve token every subcommand says the kernel is not running" {
    unset ROMP_SERVE_TOKEN
    run "$ROMP_SCRIPT" login list
    [ "$status" -eq 1 ]
    [[ "$output" == *"no serve token"* ]]
    run "$ROMP_SCRIPT" login add Work --cmd 'cat x'
    [ "$status" -eq 1 ]
    run "$ROMP_SCRIPT" login link Work --same-as PEERHOST
    [ "$status" -eq 1 ]
    [[ "$output" == *"no serve token"* ]]
    run "$ROMP_SCRIPT" login unlink Work
    [ "$status" -eq 1 ]
}

@test "login add: the kernel's 400 refusal is said with its reason, never as a kernel that is down" {
    # the kernel refuses a --cmd that carries the credential itself with a 400 and the reason in the body; read with
    # curl -f, that answer was exit 22 and no body, which the CLI reported as an unreachable kernel
    MOCK_CURL_STATUS=400 MOCK_CURL_BODY='{"ok": false, "error": "add.tokenCmd: the command text looks like it carries the credential itself; rotate it"}' \
        run "$ROMP_SCRIPT" login add Work --cmd 'cat ~/.secrets/enterprise-token'
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: refused: add.tokenCmd: the command text looks like it carries the credential itself; rotate it"* ]]
    [[ "$output" != *"not reachable"* ]]
    [[ "$output" != *"romp login: added"* ]]
    # the same answer to link, unlink and remove reads the same way
    MOCK_CURL_STATUS=400 MOCK_CURL_BODY='{"ok": false, "error": "link must be an object"}' run "$ROMP_SCRIPT" login link Work --same-as PEERHOST
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: refused: link must be an object"* ]]
    MOCK_CURL_STATUS=400 MOCK_CURL_BODY='{"ok": false, "error": "a JSON object body is required"}' run "$ROMP_SCRIPT" login unlink Work
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: refused: a JSON object body is required"* ]]
    MOCK_CURL_STATUS=400 MOCK_CURL_BODY='{"ok": false, "error": "a JSON object body is required"}' run "$ROMP_SCRIPT" login remove Work
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: refused: a JSON object body is required"* ]]
}

@test "login add: a refused serve token, an answer with no JSON reason and a kernel that never answers are each said as what they are" {
    MOCK_CURL_STATUS=403 MOCK_CURL_BODY='{"error": "token required"}' run "$ROMP_SCRIPT" login add Work --cmd 'cat x'
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: the kernel on :1 refused the serve token (HTTP 403)"* ]]
    MOCK_CURL_STATUS=502 MOCK_CURL_BODY='Bad Gateway' run "$ROMP_SCRIPT" login add Work --cmd 'cat x'
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: the kernel on :1 answered HTTP 502: Bad Gateway"* ]]
    MOCK_CURL_STATUS=500 MOCK_CURL_BODY='' run "$ROMP_SCRIPT" login add Work --cmd 'cat x'
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: the kernel on :1 answered HTTP 500 with no explanation"* ]]
    MOCK_CURL_DOWN=1 run "$ROMP_SCRIPT" login add Work --cmd 'cat x'
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: kernel not reachable on :1 (is romp running?)"* ]]
}
