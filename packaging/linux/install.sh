#!/bin/sh
# Install the Rullo standalone build for the current user (no root needed).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
prefix="${XDG_DATA_HOME:-$HOME/.local/share}"
mkdir -p "$prefix/rullo-app" "$HOME/.local/bin" "$prefix/applications" "$prefix/icons/hicolor/scalable/apps"
cp -R "$here/rullo/." "$prefix/rullo-app/"
ln -sf "$prefix/rullo-app/rullo" "$HOME/.local/bin/rullo"
# Absolute path: ~/.local/bin is not on the desktop session's PATH everywhere.
sed "s|^Exec=.*|Exec=$prefix/rullo-app/rullo|" "$here/rullo.desktop" > "$prefix/applications/rullo.desktop"
cp "$here/logo.svg" "$prefix/icons/hicolor/scalable/apps/rullo.svg"
echo "Installed. Start Rullo from your applications menu, or run: rullo"
