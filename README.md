# ESP32-S3 Multi-Radar Edge Gateway & OTA Platform v1.3.2

Complete source code for the ESP32-S3 multi-radar parking detection system with cloud OTA update capability.

## Architecture Overview

```
ESP32-S3 (Edge)
  ├── RS485 / Modbus-RTU  ──► Ultrasonic Radar × N (addr 02, 03, …)
  ├── OLED (I2C)           ──► Distance + occupancy display
  ├── Wi-Fi               ──► OTA platform (8080) + Radar platform (8081)
  └── 4G EC801E           ──► OTA firmware download (QHTTP)

Alibaba Cloud (116.62.218.129)
  ├── :8080  esp32-ota-server       ─ firmware manifest + bin hosting
  └── :8081  radar-address-binding ─ radar management, edge commands
```

## Repository Structure

```
firmware/          ESP-IDF project (ESP32-S3, target: esp32s3)
  main/            Application source (C)
  CMakeLists.txt   Project version set here (PROJECT_VER)
  sdkconfig        Board config for N16R8, UART1 RS485, UART2 4G, I2C0 OLED
  partitions_4m_ota.csv  factory + ota_0 + ota_1 (4MB each) + SPIFFS

ota-server/        8080 — OTA management server (Python/Flask)
  ota_server.py    REST API + firmware file hosting
  firmware/        manifest.json (version, sha256, url)
  html/            ota_manager.html — web UI
  deploy/          systemd service + nginx config

radar-platform/    8081 — Radar address binding platform (Python/Flask)
  radar_platform.py   Full backend: edge heartbeat, cloud commands, layout
  html/            index.html — web UI
  deploy/          systemd service + nginx config

docs/
  hardware_ota_strategy_v1.3.2.md   Full hardware/software design spec
```

## Hardware

| Item | Detail |
|------|--------|
| MCU | ESP32-S3 N16R8 |
| OLED | SSD1306 128×64, I2C0, SDA=GPIO41, SCL=GPIO42 |
| RS485 radar bus | UART1, TX=GPIO17, RX=GPIO18, 115200 baud |
| 4G module | EC801E/AT, UART2, TX=GPIO4, RX=GPIO5, 115200 baud |
| Flash partitions | factory 4MB / ota_0 4MB / ota_1 4MB / SPIFFS ~3.9MB |

## Quick Start

### 1. Build & Flash Firmware

Requires ESP-IDF v5.x.

```bash
cd firmware
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

Edit `main/app_config.h` for Wi-Fi credentials, server URLs, and GPIO assignments.

### 2. Deploy OTA Server (8080)

```bash
cd ota-server
pip install -r requirements.txt
python ota_server.py
# or: systemctl enable --now ota-server  (copy deploy/ota-server.service)
```

Place firmware `.bin` files in `firmware/` and update `firmware/manifest.json`.

### 3. Deploy Radar Platform (8081)

```bash
cd radar-platform
pip install -r requirements.txt
python radar_platform.py
# or: systemctl enable --now radar-platform
```

Server-side Modbus is **disabled by default** — radar commands are pushed to the ESP32 edge device via `POST /api/edge/devices/<deviceId>/commands`.

## Key Design Points

- **Multi-radar addressing**: On boot, ESP32 scans addresses 02–32, detects any new radar at address 01, programs it to the queue tail, and compacts gaps to keep addresses contiguous from 02 upward.
- **Edge command loop**: Cloud creates command → ESP32 polls GET …/commands → executes on RS485 locally → posts result back.
- **OTA transport split**: Wi-Fi handles telemetry + command polling; 4G (EC801E QHTTP) handles firmware download to avoid Wi-Fi size limits.
- **OLED display**: Line 0 = Wi-Fi status, Line 1 = current polled radar ID + distance, Line 2 = occupied/total count, Line 3 = firmware version.

## Version History

| Version | Notes |
|---------|-------|
| v1.3.2 | Multi-radar queue, 4G OTA validated, edge command loop proven, OLED shows ID02 fixed |
| v1.3.3 | OLED line 1 follows poll — shows each radar's distance at poll frequency |

## License

MIT
