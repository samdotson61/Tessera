#!/bin/sh
# Tessera installer (macOS / Linux).
#   curl -fsSL https://raw.githubusercontent.com/samdotson61/Tessera/main/install.sh | sh
#
# Prefers the prebuilt one-file binary from the latest GitHub release
# (no Python needed); falls back to a pip install from source into
# ~/.tessera (needs Python 3.10+). Override the install dir with TESSERA_BIN.
set -e

REPO="samdotson61/Tessera"
BIN_DIR="${TESSERA_BIN:-$HOME/.local/bin}"
OS=$(uname -s)
ARCH=$(uname -m)

asset=""
case "$OS" in
  Darwin) [ "$ARCH" = "arm64" ] && asset="tessera-macos-arm64" ;;
  Linux)  [ "$ARCH" = "x86_64" ] && asset="tessera-linux-x64" ;;
esac

path_note() {
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo ""
       echo "NOTE: $BIN_DIR is not on your PATH. Add it with:"
       echo "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
  esac
}

if [ -n "$asset" ]; then
  url="https://github.com/$REPO/releases/latest/download/$asset"
  echo "tessera: downloading the prebuilt binary ($asset) from the latest release..."
  mkdir -p "$BIN_DIR"
  # download to a temp file and rename atomically: re-installing over a
  # RUNNING tessera stays safe (overwriting a busy executable fails with
  # ETXTBSY on Linux; a rename swaps the inode instead)
  tmp="$BIN_DIR/.tessera.download.$$"
  if curl -fsSL "$url" -o "$tmp"; then
    chmod +x "$tmp"
    mv -f "$tmp" "$BIN_DIR/tessera"
    "$BIN_DIR/tessera" --help >/dev/null
    echo "tessera: installed the binary to $BIN_DIR/tessera"
    path_note
    echo ""
    echo "next:  tessera app     (opens the review UI; first run loads an offline sample)"
    exit 0
  fi
  rm -f "$tmp"
  echo "tessera: release download failed — falling back to a pip install from source."
else
  echo "tessera: no prebuilt binary for $OS/$ARCH — installing from source (needs Python 3.10+)."
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "tessera: Python 3.10+ not found and no prebuilt binary matches $OS/$ARCH." >&2
  echo "         Install Python 3.10+ and re-run, or grab a binary from:" >&2
  echo "         https://github.com/$REPO/releases" >&2
  exit 1
fi

VENV="${TESSERA_HOME:-$HOME/.tessera}/venv"
echo "tessera: creating $VENV and installing from GitHub..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
# --force-reinstall: re-running the installer always lands the CURRENT main,
# even when the version number hasn't changed (idempotent upgrade)
"$VENV/bin/pip" install --quiet --force-reinstall --no-deps \
  "git+https://github.com/$REPO.git"
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/tessera" "$BIN_DIR/tessera"
"$BIN_DIR/tessera" --help >/dev/null
echo "tessera: installed from source; command linked at $BIN_DIR/tessera"
path_note
echo ""
echo "next:  tessera app     (opens the review UI; first run loads an offline sample)"
