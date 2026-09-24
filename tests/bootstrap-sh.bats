#!/usr/bin/env bats

# ./bootstrap.sh — the one-line installer:
#   gh api -H 'Accept: application/vnd.github.raw' repos/safety-research/romp/contents/bootstrap.sh | bash
# Hermetic: HOME points at a temp dir and the "origin" is a local fixture repo whose install.sh is
# a stub, so the real install.sh never runs (it would symlink this machine's ~/.claude at the temp
# clone and break Claude Code when the temp dir is removed).

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

load git-hermetic

setup() {
    git_hermetic
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"
    mkdir -p "$HOME"
    export SHELL=/bin/zsh
    export ROMP_REPO="$TEST_DIR/origin"

    # A fake romp origin: enough structure for bootstrap's clone check, plus a
    # non-release tag and a prerelease tag alongside four releases so tag
    # selection is exercised.
    mkdir -p "$ROMP_REPO/kernel"
    printf '#!/usr/bin/env bash\necho STUB_INSTALL_RAN\n' > "$ROMP_REPO/install.sh"
    chmod +x "$ROMP_REPO/install.sh"
    touch "$ROMP_REPO/kernel/.keep"
    git -C "$ROMP_REPO" init -q -b main .
    git -C "$ROMP_REPO" add -A
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -qm init
    git -C "$ROMP_REPO" tag not-a-release
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m r1
    git -C "$ROMP_REPO" tag v0.1.0
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m r2
    git -C "$ROMP_REPO" tag v0.2.0
    # v0.9.0 then v0.10.0: a lexical sort ranks v0.9.0 first and a version sort
    # v0.10.0, so the newest release proves which order the selector uses. With
    # only v0.1.0 and v0.2.0 the two sorts agreed and the fixture could not
    # tell them apart (review find, 2026-09-08).
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m r3
    git -C "$ROMP_REPO" tag v0.9.0
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m r4
    git -C "$ROMP_REPO" tag v0.10.0
    git -C "$ROMP_REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m post-release
    # A prerelease cut after the newest release: `v*` matches it and version
    # sort ranks it first, but it is not a release.
    git -C "$ROMP_REPO" tag v9.9.9-rc.1
}

teardown() { rm -rf "$TEST_DIR"; }

@test "bootstrap.sh: clones, checks out the newest RELEASE tag, installs, sets PATH" {
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *STUB_INSTALL_RAN* ]]
    # v0.10.0, not the newer untagged commit and not the non-release tag.
    [ "$(git -C "$HOME/romp" describe --tags)" = "v0.10.0" ]
    grep -qF "$HOME/romp/bin" "$HOME/.zshrc"
}

@test "bootstrap.sh: a prerelease tag is not a release, even when version sort ranks it first" {
    # The kernel's updater compares plain vMAJOR.MINOR.PATCH numbers only, so a
    # clone installed onto v9.9.9-rc.1 would sit there with no update ever offered.
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Checking out v0.10.0"* ]]
    [[ "$output" != *"v9.9.9-rc.1"* ]]
    [ "$(git -C "$HOME/romp" describe --tags)" = "v0.10.0" ]
}

@test "bootstrap.sh: orders releases by version, not by name: v0.10.0 outranks v0.9.0" {
    # A lexical sort (`--sort=-refname`) would check out v0.9.0 here. The fixture's
    # other releases sort the same either way, so this pair is what proves the
    # order (review find, 2026-09-08).
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Checking out v0.10.0"* ]]
    [ "$(git -C "$HOME/romp" describe --tags)" = "v0.10.0" ]
}

@test "docs/install.md: the manual install uses bootstrap.sh's selector and picks the same release" {
    # The manual install spells the selector out by hand, so a change to
    # bootstrap.sh's line can pass it by: the docs kept the unfiltered `v*` pick
    # after bootstrap.sh started skipping prerelease tags, and would have checked
    # out the decoy (review find, 2026-09-08). Pin the two to the same text, and
    # run the documented line against the fixture so it is proven, not just matched.
    local want got
    want="$(grep -o "tag -l .*| head -n1" "$REPO_ROOT/bootstrap.sh")"
    got="$(grep -o "tag -l .*| head -n1" "$REPO_ROOT/docs/install.md")"
    [ -n "$want" ]
    [ "$got" = "$want" ]
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(cd "$HOME/romp" && eval "git $got")" = "v0.10.0" ]
}

@test "bootstrap.sh: re-running updates in place and does not duplicate the PATH line" {
    ROMP_DIR="$HOME/romp" bash "$REPO_ROOT/bootstrap.sh"
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Updating the romp clone"* ]]
    [ "$(grep -cF "$HOME/romp/bin" "$HOME/.zshrc")" = "1" ]
}

@test "bootstrap.sh: ROMP_REF pins a branch instead of the newest release" {
    ROMP_DIR="$HOME/romp" ROMP_REF=main run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ "$(git -C "$HOME/romp" rev-parse --abbrev-ref HEAD)" = "main" ]
}

@test "bootstrap.sh: falls back to main when no release is tagged yet" {
    git -C "$ROMP_REPO" tag -d v0.1.0 v0.2.0 v0.9.0 v0.10.0
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"No release tag published yet"* ]]
    [ "$(git -C "$HOME/romp" rev-parse --abbrev-ref HEAD)" = "main" ]
}

@test "bootstrap.sh: refuses a target directory that is not a romp clone, leaving it untouched" {
    mkdir -p "$HOME/mine" && echo keep > "$HOME/mine/important.txt"
    ROMP_DIR="$HOME/mine" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 1 ]
    [[ "$output" == *"not a romp clone"* ]]
    [ "$(cat "$HOME/mine/important.txt")" = "keep" ]
    [ ! -e "$HOME/mine/.git" ]
}

@test "bootstrap.sh: ROMP_NO_PATH leaves the shell rc alone" {
    ROMP_DIR="$HOME/romp" ROMP_NO_PATH=1 run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ ! -f "$HOME/.zshrc" ]
}

@test "install.sh: refuses to run piped from curl instead of dangling every hook symlink" {
    # Piped, \$0 is "bash", so install.sh's ROMP_DIR would resolve to the caller's
    # cwd and `ln -s` would happily point ~/.claude at paths that do not exist.
    cd "$TEST_DIR"
    run bash -c "cat '$REPO_ROOT/install.sh' | bash"
    [ "$status" -eq 1 ]
    [[ "$output" == *"cannot be piped from curl"* ]]
    [[ "$output" == *bootstrap.sh* ]]
    [ ! -e "$HOME/.claude/hooks" ]
}

# ── the publishing remote ────────────────────────────────────────────────────
# CLAUDE.md's worktree rule says publish with `git push -u fork <branch>` and never to
# origin (only main requires a pull request upstream): but a plain clone has only `origin`,
# so a fresh install could not follow the workflow the repo documents.

# ── where the clone lands ────────────────────────────────────────────────────

@test "bootstrap.sh: names the install directory and the knob, since it ignores cwd" {
    # It clones into $HOME regardless of where the one-liner is run from. Reasonable for a
    # `curl | bash`, surprising if unstated (the user asked whether it uses the cwd).
    cd "$TEST_DIR"
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Cloning romp into $HOME/romp"* ]]
    [[ "$output" == *"ROMP_DIR"* ]]
    [ ! -e "$TEST_DIR/romp" ]           # never the cwd
}

# ── the installer's blast radius ─────────────────────────────────────────────
# Installing romp is not contributing to it. bootstrap once derived <gh-user>/romp from
# whoever `gh` was logged in as and wired it as a git remote with remote.pushDefault — so an
# ordinary installer got a remote pointing at a repo they had never created, a `git push`
# aimed at it, and an unexplained line mid-install (the user 2026-07-27: don't assume the gh
# CLI, and that is beyond what an installer should configure anyway). These pin the scope so
# it cannot creep back; the 2026-09-24 move to an internal repository let gh CLONE, guarded, and nothing more.

@test "bootstrap.sh: the gh CLI is optional: absent or signed out, plain git clones and gh is asked nothing but whether it is signed in" {
    # Since the repository became internal to its organisation (2026-09-24) bootstrap MAY clone through a
    # signed-in gh, which carries the credentials an internal clone needs, but only behind a guard: gh is
    # still not a romp dependency, a machine without it installs exactly as before (ROMP_REPO, or git's own
    # credentials), and gh is never asked to configure anything. The stub records every call and reports
    # itself signed out.
    cat > "$TEST_DIR/gh" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$TEST_DIR/gh-calls"
exit 1
EOF
    chmod +x "$TEST_DIR/gh"

    PATH="$TEST_DIR:$PATH" ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ -d "$HOME/romp/.git" ]
    # with ROMP_REPO naming the origin (as this suite does) gh is not even probed; without it, the only
    # question bootstrap may put to gh is whether it is signed in
    if [ -f "$TEST_DIR/gh-calls" ]; then
        [ "$(grep -c -v -x 'auth status' "$TEST_DIR/gh-calls")" -eq 0 ]
    fi
    # the source carries exactly one signed-in probe and one guarded clone through gh, and the guard
    # itself: no other gh command, so the blast radius stays the clone
    [ "$(grep -c 'gh auth status' "$REPO_ROOT/bootstrap.sh")" -eq 1 ]
    [ "$(grep -c 'gh repo clone' "$REPO_ROOT/bootstrap.sh")" -eq 1 ]
    [ "$(grep -c 'command -v gh' "$REPO_ROOT/bootstrap.sh")" -eq 1 ]
    # every gh mention in the source, counted wherever it sits on a line: the header one-liner, the
    # relocation hint, the presence probe, the signed-in probe and the clone: five, and no sixth
    [ "$(grep -cE '(^|[^a-z])gh ' "$REPO_ROOT/bootstrap.sh")" -eq 5 ]
}

@test "bootstrap.sh: with no ROMP_REPO and a signed-in gh, the clone runs through gh over https and lands at ROMP_DIR with origin alone" {
    # The stub gh answers signed in and performs the clone from the fixture origin into the directory it
    # is handed, recording the whole call; the recorded shape is the contract (an explicit https URL, the
    # directory, then git's own --quiet behind --).
    cat > "$TEST_DIR/gh" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$TEST_DIR/gh-calls"
case "\$1 \$2" in
    "auth status") exit 0 ;;
    "repo clone") git clone --quiet "$TEST_DIR/origin" "\$4" ;;
    *) exit 1 ;;
esac
EOF
    chmod +x "$TEST_DIR/gh"
    PATH="$TEST_DIR:$PATH" ROMP_DIR="$HOME/romp" ROMP_NO_PATH=1 run env -u ROMP_REPO bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ -d "$HOME/romp/.git" ]
    [ "$(cat "$TEST_DIR/gh-calls")" = "auth status
repo clone https://github.com/safety-research/romp.git $HOME/romp -- --quiet" ]
    [ "$(git -C "$HOME/romp" remote)" = "origin" ]
}

@test "bootstrap.sh: with no ROMP_REPO and a signed-out gh, plain git clones the default URL" {
    # gh answers signed out, so the plain road runs: git clone of the default https URL. A git stub on PATH
    # records the clone and serves it from the fixture origin (no network in a test), then hands every
    # other git call to the real binary.
    REAL_GIT="$(command -v git)"
    cat > "$TEST_DIR/gh" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    cat > "$TEST_DIR/git" <<EOF
#!/usr/bin/env bash
if [ "\$1" = clone ]; then
    echo "\$*" >> "$TEST_DIR/git-calls"
    args=(); for a in "\$@"; do case "\$a" in https://*) a="$TEST_DIR/origin" ;; esac; args+=("\$a"); done
    exec "$REAL_GIT" "\${args[@]}"
fi
exec "$REAL_GIT" "\$@"
EOF
    chmod +x "$TEST_DIR/gh" "$TEST_DIR/git"
    PATH="$TEST_DIR:$PATH" ROMP_DIR="$HOME/romp" ROMP_NO_PATH=1 run env -u ROMP_REPO bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    [ -d "$HOME/romp/.git" ]
    [ "$(cat "$TEST_DIR/git-calls")" = "clone --quiet https://github.com/safety-research/romp.git $HOME/romp" ]
}

@test "bootstrap.sh: configures no git remotes or push defaults beyond the clone's own origin" {
    ROMP_DIR="$HOME/romp" run bash "$REPO_ROOT/bootstrap.sh"
    [ "$status" -eq 0 ]
    # origin, and nothing else — no `fork`, no invented publishing target.
    [ "$(git -C "$HOME/romp" remote)" = "origin" ]
    [ -z "$(git -C "$HOME/romp" config --get remote.pushDefault || true)" ]
    [[ "$output" != *"Publishing remote"* ]]
}
