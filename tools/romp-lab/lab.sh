#!/usr/bin/env bash
# romp-lab: a hermetic, headless, full-stack romp + the scripted highlight loop (see README.md).
# HERMETICITY FIRST: the state root moves to a temp dir BEFORE any romp code runs — the
# never-load-romp-modules-against-live-state rule. Nothing here may touch live state, live
# postal, or any visible display.
set -euo pipefail

# THE LAB STARTS FROM AN ALLOWLIST, NEVER FROM ITS CALLER'S ENVIRONMENT (2026-10-08). Started from inside a live
# romp session, the lab inherited that session's identity and its machine's services: ROMP_SID and
# ROMP_SESSION_NAME, the Claude Code session id the postal tools read to know who they are, the CLI's own
# messaging socket, the live kernel's port and its manager's pid, ROMP_SUPERVISED (which turns per-session systemd
# scopes on) and the user bus that lets `systemctl --user` stop the live session's units. The kernel hands its
# whole environment to every CLI it starts, so the lab's sessions, their hooks and their postal tools ran as the
# live session. A list of names to remove misses the next one anybody adds, so the script re-executes itself
# under `env -i` with only these: what a process needs to run (PATH, HOME, the user, locale, terminal, temp dir),
# where Claude Code keeps its own credentials (the lab's sessions bill through them; romp holds no key), where the
# headless browser lives, and the lab's own model knob. Everything romp needs, the lab sets itself below, through
# lab_set. No XDG_RUNTIME_DIR and no DBUS_SESSION_BUS_ADDRESS: the lab cannot reach the user's systemd manager.
LAB_ENV_KEEP="PATH HOME USER LOGNAME SHELL LANG TERM TZ TMPDIR CLAUDE_CONFIG_DIR XDG_CACHE_HOME PLAYWRIGHT_BROWSERS_PATH LAB_MODEL"
lab_env_kept() {
  case "$1" in LC_*) return 0 ;; esac
  case " $LAB_ENV_KEEP " in *" $1 "*) return 0 ;; esac
  return 1
}
if [ -z "${ROMP_LAB_REEXEC:-}" ]; then
  keep=()
  for n in $(compgen -e); do lab_env_kept "$n" && keep+=("$n=${!n}"); done
  exec env -i ${keep[@]+"${keep[@]}"} ROMP_LAB_REEXEC=1 "$BASH" "$0" "$@"
fi
# Read back, every entry: bash does not import a name it cannot hold as a variable (or an exported function's), yet
# hands it to its children, so only a full read proves the allowlist. A caller that set the marker itself skipped
# the re-exec and is refused here, as is an environment that cannot be read.
if ! extra="$(python3 -c 'import os, sys
keep = set(sys.argv[1].split()) | {"PWD", "OLDPWD", "SHLVL", "_", "ROMP_LAB_REEXEC"}
print(" ".join(sorted(n for n in os.environ if n not in keep and not n.startswith("LC_"))))' "$LAB_ENV_KEEP")" || [ -n "$extra" ]; then
  echo "romp-lab: refusing to start: the environment carries names outside the lab's allowlist (${extra:-unreadable})" >&2
  exit 2
fi
unset ROMP_LAB_REEXEC

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KEEP=0
MODE=all
HOSTS=off
for a in "$@"; do case "$a" in
  --keep) KEEP=1 ;;
  --session-hosts) HOSTS=on ;;        # opt in to per-session hosts (off by default: a lab spawns no host process)
  --banner-only) MODE=banner ;;       # the T119 reload-banner phase alone (no model spend)
  --highlight-only) MODE=highlight ;; # the T102/T106 highlight loop alone
  --modes-only) MODE=modes ;;         # the T139 permission-mode sweep alone
  --login-only) MODE=login ;;         # the T157 in-dashboard login flow (mocked CLI, no model spend)
  --env-only) MODE="env" ;;           # what the lab kernel and its CLIs were handed (probe CLI, no build, no browser, no spend)
esac; done

LAB="$(mktemp -d "${TMPDIR:-/tmp}/romp-lab-XXXXXX")"
mkdir -p "$LAB/state" "$LAB/shots" "$LAB/project"
echo "# a synthetic scratch project for the lab session" > "$LAB/project/README.md"

# Every name the lab puts into its kernel's environment goes through lab_set, so the environment phase can check the
# kernel carries nothing else (env-loop.py, LAB_ENV_ALLOWED).
LAB_OWN=""
lab_set() { export "$1=$2"; LAB_OWN="$LAB_OWN $1"; }

lab_set XDG_STATE_HOME "$LAB/state"
# per-session hosts are ON by default on a machine (T348), so a state root with no toggle would start a real
# romp-session-host for every lab session; the lab's root says off unless the caller asked for hosts
mkdir -p "$LAB/state/romp"
echo "$HOSTS" > "$LAB/state/romp/session-hosts"
# no per-session systemd scopes either (the kernel turns them on under ROMP_SUPERVISED, which the lab no longer
# inherits; said outright so no other road turns them on)
lab_set ROMP_CLI_SCOPE 0
# never the machine's real manager: an inherited ROMP_MANAGER_PORT would let the lab kernel
# restart the live deployment. Poisoned to a dead port, not unset — absent maps to the DEFAULT
# (live) port in the update path, so only a dead value is safe against every consumer.
lab_set ROMP_MANAGER_PORT 1
# the SDK venv is PACKAGES, not state — symlink the machine's real one into the hermetic root so
# lab sessions can actually run their CLI (without it every SDK session reports unable to start).
# A symlink shares bytes only; nothing here writes into the venv.
REAL_VENV="${HOME}/.local/state/romp/sdkvenv"
if [ -d "$REAL_VENV" ]; then
  mkdir -p "$LAB/state/romp"
  ln -s "$REAL_VENV" "$LAB/state/romp/sdkvenv"
fi
lab_set ROMP_KERNEL_NO_OPEN 1
lab_set ROMP_SERVE_TOKEN "labtok-$(head -c8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
free_port() {   # bind 0 and read it back
  python3 - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
}
PORT=$(free_port)
lab_set ROMP_KERNEL_PORT "$PORT"
lab_set ROMP_SERVE_PORT "$PORT"         # the hooks and the manager read this spelling first; absent means the live default
# no update check in any mode (the review of #173): its notice offers a one-click update, and the click fast-forwards
# the checkout this script lives in, which may be the one a live kernel serves (and a machine that mirrors it restarts)
lab_set ROMP_UPDATE_CHECK off
# a postal bus of the lab's own, never started: the port is the run's own (HERMETIC), the legacy singleton scheme
# (PEERS=0) honours CLIENT_ONLY, so the lab's sessions dial a port nothing listens on and never the machine's bus
lab_set ROMP_POSTAL_PORT "$(free_port)"
lab_set ROMP_POSTAL_PEERS 0
lab_set ROMP_POSTAL_CLIENT_ONLY 1
lab_set ROMP_POSTAL_HERMETIC 1

# serve the FRESH build, never a stale bundle (the T106 triage's first lesson); the environment phase serves no page
if [ "$MODE" != "env" ]; then
  ( cd "$ROOT/vscode-extension" && node esbuild.js >/dev/null 2>&1 )
fi

# …and serve a COPY of it (T119): the banner phase simulates rebuilds by bumping dist mtimes, and
# the repo's real dist is what the LIVE kernel serves — a lab run must never raise reload banners
# on the user's real dashboard. ROMP_DIST_DIR is the kernel's test seam for exactly this; it also
# stands the kernel's own dist-vs-source converge down, so the lab controls every mtime it asserts.
mkdir -p "$LAB/dist"
cp "$ROOT/vscode-extension/dist/"*.js "$LAB/dist/" 2>/dev/null || true
cp "$ROOT/vscode-extension/dist/"*.css "$LAB/dist/" 2>/dev/null || true
if [ -d "$ROOT/vscode-extension/dist/fonts" ]; then cp -r "$ROOT/vscode-extension/dist/fonts" "$LAB/dist/fonts"; fi
lab_set ROMP_DIST_DIR "$LAB/dist"
# a fast heartbeat so the banner phase's one-heartbeat assertions run in seconds, not tens of them
lab_set ROMP_WS_KEEPALIVE 2

if [ "$MODE" = "login" ]; then
  # the T157 phase drives the LOGIN flow only (no sessions run): the kernel resolves claude through
  # ROMP_CLAUDE_BIN, so pointing it at the mock makes the whole flow synthetic — never a real account
  lab_set ROMP_CLAUDE_BIN "$ROOT/tools/romp-lab/login-mock-claude.py"
  lab_set LOGIN_MOCK_MARKER "$LAB/login-code-sha.txt"
fi
if [ "$MODE" = "env" ]; then
  # the environment phase's CLI is the PROBE: every CLI the kernel starts writes down what it was handed, then exits
  lab_set ROMP_CLAUDE_BIN "$ROOT/tools/romp-lab/env-probe-cli.py"
  lab_set ROMP_LAB_PROBE_DIR "$LAB/probe"
  lab_set ROMP_MODEL_CATALOG off        # no network in this phase: no model catalog fetch
fi
"$ROOT/bin/romp-kernel" > "$LAB/kernel.log" 2>&1 &
KPID=$!
cleanup() {
  # the banner phase restarts the kernel (its reconnect assertion) and records the new pid
  [ -f "$LAB/kernel.pid" ] && KPID="$(cat "$LAB/kernel.pid")"
  kill "$KPID" 2>/dev/null || true
  wait "$KPID" 2>/dev/null || true
  # a session host outlives its kernel by design, so a --session-hosts lab's hosts (and a CLI mid-turn, spending) would
  # run on after the lab is gone: end the ones this lab's own state root records, once the kernel can spawn no more
  if [ -d "$LAB/state/romp/hosts" ] || [ -d "$LAB/state/romp/leases" ]; then
    python3 "$ROOT/tools/romp-lab/end-hosts.py" "$LAB/state/romp" || echo "romp-lab: ending the lab's hosts failed (see above)" >&2
  fi
  if [ "$KEEP" = 1 ]; then echo "kept: $LAB (kernel.log, shots/)"; else rm -rf "$LAB"; fi
}
trap cleanup EXIT

for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then break; fi
  sleep 0.5
  kill -0 "$KPID" 2>/dev/null || { echo "kernel died at boot — $LAB/kernel.log:"; tail -20 "$LAB/kernel.log"; exit 1; }
done
curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null || { echo "kernel never became healthy"; exit 1; }
echo "lab kernel up on :$PORT (state: $LAB/state)"

RC=0
if [ "$MODE" = "env" ]; then
  LAB_DIR="$LAB" PORT="$PORT" TOKEN="$ROMP_SERVE_TOKEN" KPID="$KPID" PROJECT_DIR="$LAB/project" \
    LAB_ENV_ALLOWED="$LAB_ENV_KEEP$LAB_OWN" python3 "$ROOT/tools/romp-lab/env-loop.py" || RC=$?
  echo "env loop exit: $RC (evidence: $LAB/probe)"
fi
if [ "$MODE" != "highlight" ] && [ "$MODE" != "modes" ] && [ "$MODE" != "login" ] && [ "$MODE" != "env" ]; then
  # the reload-banner contract (T119) — first, because it spends no model turns
  LAB_DIR="$LAB" PORT="$PORT" TOKEN="$ROMP_SERVE_TOKEN" KPID="$KPID" KERNEL_BIN="$ROOT/bin/romp-kernel" \
    node "$ROOT/tools/romp-lab/banner-loop.mjs" || RC=$?
  echo "banner loop exit: $RC (shots: $LAB/shots)"
  [ -f "$LAB/kernel.pid" ] && KPID="$(cat "$LAB/kernel.pid")"
fi
if [ "$MODE" != "banner" ] && [ "$MODE" != "modes" ] && [ "$MODE" != "login" ] && [ "$MODE" != "env" ] && [ "$RC" = 0 ]; then
  # the kernel may have been bounced by the banner phase — wait for health before driving it
  for i in $(seq 1 60); do
    curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5
  done
  LAB_DIR="$LAB" PORT="$PORT" TOKEN="$ROMP_SERVE_TOKEN" PROJECT_DIR="$LAB/project" \
    node "$ROOT/tools/romp-lab/highlight-loop.mjs" || RC=$?
  echo "highlight loop exit: $RC (shots: $LAB/shots)"
fi
if [ "$MODE" = "login" ] && [ "$RC" = 0 ]; then
  LAB_DIR="$LAB" PORT="$PORT" TOKEN="$ROMP_SERVE_TOKEN" LOGIN_MOCK_MARKER="$LOGIN_MOCK_MARKER" \
    node "$ROOT/tools/romp-lab/login-loop.mjs" || RC=$?
  echo "login loop exit: $RC (shots: $LAB/shots)"
fi
if [ "$MODE" != "banner" ] && [ "$MODE" != "highlight" ] && [ "$MODE" != "login" ] && [ "$MODE" != "env" ] && [ "$RC" = 0 ]; then
  # the T139 permission-mode sweep: every mode's ask contract on the real stack
  for i in $(seq 1 60); do
    curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5
  done
  LAB_DIR="$LAB" PORT="$PORT" TOKEN="$ROMP_SERVE_TOKEN" PROJECT_DIR="$LAB/project" \
    node "$ROOT/tools/romp-lab/modes-loop.mjs" || RC=$?
  echo "modes loop exit: $RC (shots: $LAB/shots)"
fi
[ "$RC" = 0 ] || KEEP=1
exit "$RC"
