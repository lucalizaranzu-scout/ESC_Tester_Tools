# Vertiq ESC PWM Configurator

A one-window Linux app that switches a Vertiq ESC motor into PWM control mode, or back, over an FTDI serial adapter. You can use it instead of IQ Control Center for this task.

## Install / run

```bash
./install.sh   # installs python3-tk / pyserial if missing, adds you to the dialout group and creates an app-menu entry
./run.sh       # or launch "Vertiq ESC PWM Configurator" from the app menu
./create_desktop_shortcut.sh   # optional: put a launcher icon on the desktop
```

## Using it

1. **Connect**: opens `/dev/ttyUSB0` at 115200 baud and finds the module. If the motor isn't on module ID 0, it scans the other IDs. The status line shows whether the motor already matches the PWM profile.
2. **Set PWM Parameters**: reads the motor's current values and saves them as a backup for that motor. It then writes the PWM profile, saves it to flash and reads every value back to check it.
3. **Revert to CAN**: writes the `can` profile, which takes the motor out of PWM (ESC) mode and back into DroneCAN mode. If no CAN profile has been captured yet, it falls back to the backup that **Set** took for that motor.

**Set** and **Revert to CAN** restart the motor over serial when they finish, so you don't need to power-cycle it. The app then reconnects to confirm the motor came back. **Restart Motor** does the same restart on its own.

## One-time setup: capture the PWM profile

`profile.json` holds the values that **Set** writes. The included profile is only a starting guess: it sets `esc_propeller_input_parser.mode = 0`. To match your exact setup:

1. Configure one motor the way you normally do in IQ Control Center.
2. Connect it in this app, then choose **Advanced → Capture PWM profile from connected motor**.
3. On a motor that is set up for CAN, use **Advanced → Capture CAN profile from connected motor**. **Revert to CAN** then works on every motor, including ones this app never configured.

The profiles never include the per-motor DroneCAN node ID, ESC index or actuator ID, so each motor keeps its own.

You can edit `profile.json` by hand to keep only the parameters you actually change. Give users the folder with that `profile.json` in it.

Per-motor backups are stored in `~/.config/vertiq-pwm-configurator/backups/<UID>.json`.

## Files

- `app.py`: Tkinter GUI
- `iquart.py`: minimal IQUART serial protocol (the same packet format and CRC as Vertiq's `iqmotion` library) plus the parameter table
- `profile.json`: port, baud, `pwm` values and `can` values
