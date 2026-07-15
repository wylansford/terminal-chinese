#!/usr/bin/env bash
set -euo pipefail

MODE="production"
if [[ "${1:-}" == "--dev" ]]; then
    MODE="dev"
    shift
elif [[ $# -gt 0 ]]; then
    echo "Usage: $0 [--dev]" >&2
    exit 2
fi

DEFAULT_APP_DIR="$HOME/.local/share/terminal-chinese"
DEFAULT_CONFIG_DIR="$HOME/.config/terminal-chinese"
BIN_NAME="terminal-chinese"
if [[ "$MODE" == "dev" ]]; then
    DEFAULT_APP_DIR="$HOME/.local/share/terminal-chinese-dev"
    DEFAULT_CONFIG_DIR="$HOME/.config/terminal-chinese-dev"
    BIN_NAME="terminal-chinese-dev"
fi
APP_DIR="${TERMINAL_CHINESE_HOME:-$DEFAULT_APP_DIR}"
CONFIG_DIR="$DEFAULT_CONFIG_DIR"

echo "🗑️  Uninstalling terminal-chinese ($MODE)..."
echo ""
echo "This removes the app ($APP_DIR) and all data in $CONFIG_DIR"
echo "(database, learning progress, vocabulary)."
read -p "Are you sure? [y/N] " -n 1 -r; echo
[[ $REPLY =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }

read -p "Create backup of your data first? [Y/n] " -n 1 -r; echo
if [[ ! $REPLY =~ ^[Nn]$ && -d "$CONFIG_DIR" ]]; then
    BACKUP_FILE="$HOME/terminal-chinese-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    tar -czf "$BACKUP_FILE" -C "$HOME/.config" "terminal-chinese"
    echo "✓ Backup saved to: $BACKUP_FILE"
fi

rm -rf "$CONFIG_DIR" "$APP_DIR"
rm -f "$HOME/.local/bin/$BIN_NAME"

cat <<'EOF'

✅ Uninstalled.

To finish, remove the terminal-chinese block from your ~/.zshrc (or
~/.bashrc) -- it starts with the comment "# terminal-chinese" and
ends at the matching "fi".

Installed via Homebrew instead? Use: brew uninstall terminal-chinese
EOF
