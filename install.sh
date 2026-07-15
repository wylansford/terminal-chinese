#!/usr/bin/env bash
# terminal-chinese installer (macOS & Linux)
#   curl -fsSL https://raw.githubusercontent.com/wylansford/terminal-chinese/main/install.sh | bash
set -euo pipefail

REPO="https://github.com/wylansford/terminal-chinese"
APP_DIR="${TERMINAL_CHINESE_HOME:-$HOME/.local/share/terminal-chinese}"
BIN_DIR="$HOME/.local/bin"

echo "🇨🇳 Installing terminal-chinese..."

command -v python3 >/dev/null || { echo "❌ python3 is required."; exit 1; }
command -v git >/dev/null || { echo "❌ git is required."; exit 1; }

if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull -q
else
    git clone -q --depth 1 "$REPO" "$APP_DIR"
fi

if ! python3 -m venv "$APP_DIR/.venv" 2>/dev/null; then
    echo "❌ python3-venv is missing. On Ubuntu/Debian: sudo apt install python3-venv"
    exit 1
fi
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/terminal-chinese" <<EOF
#!/bin/sh
exec "$APP_DIR/.venv/bin/python3" "$APP_DIR/bin/terminal-chinese" "\$@"
EOF
chmod +x "$BIN_DIR/terminal-chinese"

# Hook the shell so cards appear on terminal startup. The hook text is
# generated once, here, and written as-is -- not re-generated via
# `eval "$(...)"` on every shell, which would cost a full Python startup
# on every subshell just to reproduce identical text.
case "${SHELL:-}" in
    */bash) RC_FILE="$HOME/.bashrc"; SHELL_NAME="bash" ;;
    *)      RC_FILE="$HOME/.zshrc";  SHELL_NAME="zsh"  ;;
esac
MARKER="# terminal-chinese — vocabulary card on terminal startup"

if grep -qs "$MARKER" "$RC_FILE"; then
    HOOK_MSG="terminal-chinese is already wired into $RC_FILE."
else
    HOOK_TEXT="$(TERMINAL_CHINESE_BIN="$BIN_DIR/terminal-chinese" "$BIN_DIR/terminal-chinese" init "$SHELL_NAME")"
    {
        echo ""
        echo "$MARKER (terminal-chinese.lansford.dev)"
        echo "$HOOK_TEXT"
    } >> "$RC_FILE"
    HOOK_MSG="Just added terminal-chinese to $RC_FILE! It'll run automatically on every terminal startup from now on."
fi

cat <<EOF

✅ $HOOK_MSG
   Open a new terminal to see your first card — HSK 1-6 vocabulary ships in
   the box, starting with HSK 1 (widen anytime with: ct config --hsk 1,2,3).
   Happy learning! 加油！

Re-run this script any time to update. Uninstall: uninstall.sh in the repo,
or simply: rm -rf ~/.local/share/terminal-chinese ~/.local/bin/terminal-chinese
EOF
