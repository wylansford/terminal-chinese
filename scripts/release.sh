#!/usr/bin/env bash
# Release terminal-chinese to GitHub and the Homebrew tap.
# Usage: scripts/release.sh 1.1.4
# Recovery: scripts/release.sh --tap-only 1.1.3
set -euo pipefail

REPO="wylansford/terminal-chinese"
TAP_REPO="wylansford/homebrew-tap"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAP_DIR=""

cleanup() {
    if [[ -n "$TAP_DIR" && -d "$TAP_DIR" ]]; then
        rm -rf "$TAP_DIR"
    fi
}
trap cleanup EXIT

usage() {
    echo "Usage: $0 VERSION" >&2
    echo "       $0 --tap-only VERSION" >&2
    exit 2
}

TAP_ONLY=false
if [[ "${1:-}" == "--tap-only" ]]; then
    TAP_ONLY=true
    shift
fi
VERSION="${1:-}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || usage
TAG="v$VERSION"

command -v gh >/dev/null || { echo "gh is required" >&2; exit 1; }
command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v shasum >/dev/null || { echo "shasum is required" >&2; exit 1; }
gh auth status >/dev/null

if [[ "$TAP_ONLY" == false ]]; then
    [[ "$(git -C "$ROOT" branch --show-current)" == "main" ]] || {
        echo "release must run from main" >&2
        exit 1
    }
    [[ -z "$(git -C "$ROOT" status --short)" ]] || {
        echo "working tree must be clean" >&2
        exit 1
    }
    git -C "$ROOT" pull --ff-only origin main
    python3 -m unittest discover -s "$ROOT/tests" -v
    python3 -m py_compile "$ROOT/bin/terminal-chinese" "$ROOT"/lib/*.py
    bash -n "$ROOT/install.sh" "$ROOT/uninstall.sh"

    if git -C "$ROOT" rev-parse "$TAG" >/dev/null 2>&1; then
        echo "$TAG already exists; use --tap-only if the release is already published" >&2
        exit 1
    fi
    git -C "$ROOT" tag -a "$TAG" -m "Release $TAG"
    git -C "$ROOT" push origin "$TAG"
    gh release create "$TAG" --repo "$REPO" --title "$TAG" \
        --generate-notes
fi

ARCHIVE="$(mktemp -t terminal-chinese-release).tar.gz"
curl -fsSL "https://github.com/$REPO/archive/refs/tags/$TAG.tar.gz" -o "$ARCHIVE"
SHA256="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
TAP_DIR="$(mktemp -d -t terminal-chinese-tap)"
git clone -q "https://github.com/$TAP_REPO.git" "$TAP_DIR"
FORMULA="$TAP_DIR/Formula/terminal-chinese.rb"

VERSION="$VERSION" SHA256="$SHA256" perl -0pi -e \
    's#archive/refs/tags/v[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz#archive/refs/tags/v$ENV{VERSION}.tar.gz#; \
     s/sha256 "[0-9a-f]+"/sha256 "$ENV{SHA256}"/' "$FORMULA"

git -C "$TAP_DIR" diff --exit-code -- "$FORMULA" >/dev/null && {
    echo "Homebrew formula already points to $TAG"
    exit 0
}
git -C "$TAP_DIR" add "$FORMULA"
git -C "$TAP_DIR" commit -m "terminal-chinese: bump to $TAG"
git -C "$TAP_DIR" push origin main

echo "Released $TAG"
echo "Homebrew formula updated with SHA256 $SHA256"
