#!/bin/sh
# Installs the app launcher for the current user.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/share/applications"
sed "s|@DIR@|$DIR|" "$DIR/audio-output-settings.desktop.in" > "$HOME/.local/share/applications/audio-output-settings.desktop"
echo "Installed. Look for \"Audio Output Settings\" in your app menu."
