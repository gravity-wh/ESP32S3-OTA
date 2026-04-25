# ESP32-S3 Flashing Guide (WSL/Container Passthrough)

This project provides VS Code tasks for robust flashing in passthrough setups.

## Recommended Task Order

1. Run `esp: chip-id (115200)` to verify serial handshake.
2. If handshake fails, run `esp: flash-safe (manual boot, 115200)`.
3. After stable flashing, use `esp: flash-fast (auto reset, 460800)`.

## Manual Download Mode (for `flash-safe`)

1. Hold `BOOT`.
2. Tap `EN/RST`.
3. Release `EN/RST`.
4. Release `BOOT`.
5. Press Enter in the task terminal to start flashing.

The `flash-safe` task uses:

- `--before no-reset`
- `--after no-reset`
- `115200` baud

This avoids unreliable auto-reset behavior over USB passthrough.

## If `/dev/ttyACM0` Still Fails

1. Replug the USB cable and confirm the port exists in the container:
   - `ls -l /dev/ttyACM* /dev/ttyUSB*`
2. Re-attach USB passthrough (usbipd/devcontainer).
3. Make sure no other process is holding the port:
   - `lsof /dev/ttyACM0`
4. As final fallback, flash from host Windows using native COM port (no passthrough).

## Wi-Fi OTA Test Demo

This project now includes an OTA demo in `main/main.c`:

- Device joins your router in STA mode.
- After getting an IP, it downloads firmware from `OTA_URL`.
- OTA succeeds, then it reboots into the new app partition.

Before build/flash, update:

- `WIFI_SSID`
- `WIFI_PASSWORD`
- `OTA_URL` (HTTP URL to your `.bin`)

Example local test flow:

1. Build first firmware and flash:
   - `idf.py build flash monitor`
2. On your PC, host the generated app binary:
   - `python -m http.server 8000 --directory build`
3. Ensure `OTA_URL` points to:
   - `http://<PC_LAN_IP>:8000/sample_project.bin`
4. Reboot ESP32 and watch logs for `OTA success, reboot now`.

To verify OTA actually switched firmware, change `APP_VERSION` in `main/main.c` (for example `v1.0.0` -> `v2.0.0`), rebuild, and host the new `sample_project.bin` for the OTA run.
