#!/usr/bin/env bash
# Installs dependencies and adds "Vertiq ESC PWM Configurator" to the applications menu.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
python3 -c "import tkinter" 2>/dev/null || sudo apt-get install -y python3-tk
python3 -c "import serial" 2>/dev/null || sudo apt-get install -y python3-serial
if ! id -nG "$USER" | grep -qw dialout; then
  sudo usermod -aG dialout "$USER"
  echo "Added $USER to the 'dialout' group - log out and back in for serial access."
fi
chmod +x "$DIR/run.sh" "$DIR/app.py"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/vertiq-pwm-configurator.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Vertiq ESC PWM Configurator
Comment=Switch Vertiq ESC motors to/from PWM control
Exec=$DIR/run.sh
Icon=preferences-system
Terminal=false
Categories=Utility;Development;
DESK
echo "Installed. Launch 'Vertiq ESC PWM Configurator' from the app menu, or run $DIR/run.sh"
