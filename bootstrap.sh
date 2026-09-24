#!/usr/bin/env bash
# One-line install for romp:
#   gh api -H 'Accept: application/vnd.github.raw' repos/safety-research/romp/contents/bootstrap.sh | bash
# (the repository is internal to its organisation, so the signed-in GitHub CLI fetches the script and clones)
#
# Clones romp, checks out the newest release, runs install.sh, and puts bin/ on
# your PATH. The clone IS the installation (install.sh symlinks the hooks, MCP
# config and skills out of it, and bin/ links back into it), so this keeps the
# clone at a stable location rather than a temp dir.
#
# Knobs:
#   ROMP_DIR=~/romp     where to clone (default ~/romp)
#   ROMP_REF=main       install a specific tag/branch instead of the newest release
#   ROMP_NO_PATH=1      don't touch your shell rc
#   ROMP_REPO=<url>     clone this URL with plain git (an SSH one, say); setting it also turns the gh road off
# install.sh's own switches (ROMP_NO_EXT, ROMP_NO_SERVICE, ROMP_NO_SDK) pass through.
set -euo pipefail

SLUG="safety-research/romp"
REPO="${ROMP_REPO:-https://github.com/$SLUG.git}"
DIR="${ROMP_DIR:-$HOME/romp}"

command -v git >/dev/null 2>&1 || {
    echo "romp: git not found. Install git and re-run." >&2; exit 1; }

# Clone, or reuse an existing clone. Refuse to touch a directory that is not a
# romp checkout: this script writes into it and checks out refs, which would be
# destructive to somebody else's files.
if [ -e "$DIR" ]; then
    if [ -d "$DIR/.git" ] && [ -f "$DIR/install.sh" ] && [ -d "$DIR/kernel" ]; then
        echo "==> Updating the romp clone at $DIR"
        git -C "$DIR" fetch --quiet --tags origin
    else
        echo "romp: $DIR exists and is not a romp clone. Refusing to write into it." >&2
        echo "  Move it aside, or choose another location:" >&2
        echo "    gh api -H 'Accept: application/vnd.github.raw' repos/$SLUG/contents/bootstrap.sh | ROMP_DIR=\"\$HOME/elsewhere\" bash" >&2
        exit 1
    fi
else
    # Name the destination and the knob in the same breath. The clone lands in $HOME
    # regardless of where you run the one-liner from — reasonable for a `curl | bash`
    # (your cwd could be /, /tmp, or somebody else's repo), but surprising if unstated
    # (the user 2026-07-27 asked whether it installs into the current directory).
    echo "==> Cloning romp into $DIR   (override with ROMP_DIR=/path)"
    # An internal repository needs credentials: the signed-in GitHub CLI clones with its own (and its
    # configured protocol); ROMP_REPO names another URL (an SSH one, say) for plain git to clone.
    if [ -z "${ROMP_REPO:-}" ] && command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
        gh repo clone "https://github.com/$SLUG.git" "$DIR" -- --quiet
    else
        git clone --quiet "$REPO" "$DIR"
    fi
fi

# Pick the ref. A release is exactly `vMAJOR.MINOR.PATCH`, so match on that
# rather than taking the newest tag of any kind: the repo also carries
# non-release tags, and installing one of those would silently pin somebody to
# an old baseline. Nor does the `v*` glob alone suffice: git's version sort
# (`--sort=v:refname`, git-tag(1)) compares the digit runs in a tag name as
# numbers, so a prerelease cut for the next version (`v0.3.0-rc.1` after
# `v0.2.0`) outranks the newest release on its number alone, and unless
# `versionsort.suffix` is configured a suffixed tag also sorts above the plain
# release of the same version (`v0.3.0-rc.1` above `v0.3.0`, git-config(1)).
# The kernel's updater only compares plain release numbers, so a clone
# installed onto such a tag would never see another update. (Reworded on a
# review find, 2026-09-08: the sort does not single prereleases out; their
# numbers do.)
ref="${ROMP_REF:-}"
if [ -z "$ref" ]; then
    ref="$(git -C "$DIR" tag -l 'v*' --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n1 || true)"
    if [ -z "$ref" ]; then
        ref=main
        echo "    No release tag published yet, so installing the latest code (main)."
    fi
fi

echo "==> Checking out $ref"
git -C "$DIR" checkout --quiet "$ref"
# Fast-forward when the ref is a branch; a tag leaves a detached HEAD, where
# pull is meaningless and expected to fail.
git -C "$DIR" pull --quiet --ff-only >/dev/null 2>&1 || true

echo "==> Running install.sh"
"$DIR/install.sh"

# Put bin/ on PATH. Idempotent: keyed on the exact line, so re-running this
# script never stacks up duplicates.
if [ -z "${ROMP_NO_PATH:-}" ]; then
    case "$(basename "${SHELL:-}")" in
        zsh)  rc="$HOME/.zshrc";  line="export PATH=\"\$PATH:$DIR/bin\"" ;;
        bash) if [ -f "$HOME/.bashrc" ]; then rc="$HOME/.bashrc"
              else rc="$HOME/.bash_profile"; fi
              line="export PATH=\"\$PATH:$DIR/bin\"" ;;
        fish) rc="$HOME/.config/fish/config.fish"; line="fish_add_path $DIR/bin" ;;
        *)    rc=""; line="export PATH=\"\$PATH:$DIR/bin\"" ;;
    esac
    if [ -n "$rc" ]; then
        mkdir -p "$(dirname "$rc")"
        if [ -f "$rc" ] && grep -qF "$DIR/bin" "$rc"; then
            echo "    PATH already set in $rc"
        else
            printf '\n# romp\n%s\n' "$line" >> "$rc"
            echo "    Added romp to your PATH in $rc"
        fi
    else
        echo "    Unknown shell. Add this to your shell rc yourself:"
        echo "      $line"
    fi
fi

echo
echo "romp is installed at $DIR"
echo "Open a new terminal (or 'source' your shell rc), then run:  romp"
