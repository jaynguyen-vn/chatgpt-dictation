#!/usr/bin/env bash
# Install the dependencies through Homebrew and load dictation.lua into Hammerspoon. Safe to run repeatedly:
# anything already installed is skipped, and the dofile line is added only once.
#   ./install.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
INIT="$HOME/.hammerspoon/init.lua"
LOAD_LINE="dofile(\"$DIR/dictation.lua\")"

fail() { echo "Error: $*" >&2; exit 1; }

[[ "$(uname)" == "Darwin" ]] || fail "macOS only."
command -v brew >/dev/null || fail "Homebrew not found. Install it from https://brew.sh first."
# Hammerspoon runs /usr/bin/python3; without the Command Line Tools that command only pops up an installer.
xcode-select -p >/dev/null 2>&1 || fail "Command Line Tools not found. Run: xcode-select --install"

for formula in ffmpeg curl; do
  if brew list --formula "$formula" >/dev/null 2>&1; then
    echo "ok   $formula"
  else
    brew install "$formula"
  fi
done

if [[ -d /Applications/Hammerspoon.app ]]; then
  echo "ok   Hammerspoon"
else
  brew install --cask hammerspoon
fi

mkdir -p "$(dirname "$INIT")"
touch "$INIT"
if grep -qF "$LOAD_LINE" "$INIT"; then
  echo "ok   $INIT already loads dictation.lua"
else
  printf '\n%s\n' "$LOAD_LINE" >> "$INIT"
  echo "add  $LOAD_LINE -> $INIT"
fi

if [[ ! -f "$HOME/.codex/auth.json" ]]; then
  echo
  echo "Warning: Codex is not logged in. Install Codex (the app or the CLI) and run: codex login"
  echo "         Sign in with your ChatGPT account, not an API key."
fi

cat <<'EOF'

Next steps:
  1. Open Hammerspoon and choose "Reload Config" from its menu bar icon.
  2. Grant Hammerspoon Accessibility access (System Settings → Privacy & Security → Accessibility).
  3. Hold Right Option and speak. Allow microphone access when macOS asks.
  Optional: enable "Launch Hammerspoon at login" in Hammerspoon's Preferences.
EOF
