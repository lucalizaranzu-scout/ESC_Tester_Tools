#!/usr/bin/env bash
# Puts a "Vertiq ESC PWM Configurator" shortcut on the desktop pointing at this folder.
set -e
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
SHORTCUT="$DESKTOP/vertiq-pwm-configurator.desktop"

chmod +x "$DIR/run.sh" "$DIR/app.py"
mkdir -p "$DESKTOP"
cat > "$SHORTCUT" <<DESK
[Desktop Entry]
Type=Application
Name=Vertiq ESC PWM Configurator
Comment=Switch Vertiq ESC motors between PWM and CAN mode
Exec=$DIR/run.sh
Path=$DIR
Icon=preferences-system
Terminal=false
Categories=Utility;
DESK
chmod +x "$SHORTCUT"
# Mark as trusted so GNOME/Cinnamon launch it without an "untrusted launcher" prompt
gio set "$SHORTCUT" metadata::trusted true 2>/dev/null || true
echo "Shortcut created: $SHORTCUT"
