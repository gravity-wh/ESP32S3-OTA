#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雷达地址绑定平台后端服务

能力：
- 停车位表格布局管理（罗马数字行 / Excel 风格列）
- 车位与雷达地址绑定
- RS485 / Modbus-RTU 地址编程、寄存器读写、地址扫描
- 雷达状态轮询、状态推导与日志记录
- 支持模拟模式，便于无硬件联调
"""

import datetime
import json
import logging
import math
import os
import threading
import time
import uuid

from flask import Flask, jsonify, request

try:
    import serial
except Exception:  # pragma: no cover - optional dependency
    serial = None

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_DIR = os.path.join(BASE_DIR, "html")
LOG_DIR = os.environ.get("RADAR_PLATFORM_LOG_DIR", "/var/log/radar_binding_platform")
DATA_FILE = os.environ.get("RADAR_PLATFORM_DATA_FILE", os.path.join(LOG_DIR, "platform_data.json"))
LOG_FILE = os.environ.get("RADAR_PLATFORM_LOG_FILE", os.path.join(LOG_DIR, "platform_logs.json"))
SERVER_LOG_FILE = os.environ.get("RADAR_PLATFORM_SERVER_LOG_FILE", os.path.join(LOG_DIR, "platform_server.log"))
PUBLIC_BASE_URL = os.environ.get("RADAR_PLATFORM_PUBLIC_BASE_URL", "http://116.62.218.129:8081").rstrip("/")
SERVER_MODBUS_ENABLED = os.environ.get("RADAR_PLATFORM_ENABLE_SERVER_MODBUS", "0").strip().lower() in ("1", "true", "yes", "on")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(HTML_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(SERVER_LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

TRANSPORT_FACTORY = None
POLL_THREAD = None
POLL_STOP_EVENT = threading.Event()
POLL_LOCK = threading.Lock()


def now():
    return datetime.datetime.now()


def now_str():
    return now().strftime("%Y-%m-%d %H:%M:%S")


def parse_time(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def coerce_int(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def coerce_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def bool_value(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def normalize_tags(tags):
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = [item.strip() for item in tags.replace("，", ",").split(",")]
    if not isinstance(tags, list):
        return []
    result = []
    for item in tags:
        value = str(item).strip()
        if value and value not in result:
            result.append(value)
    return result


def excel_column_label(index):
    value = int(index)
    if value <= 0:
        raise ValueError("column index must be positive")
    result = []
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        result.append(chr(65 + remainder))
    return "".join(reversed(result))


ROMAN_TABLE = (
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)


def roman_label(index):
    value = int(index)
    if value <= 0:
        raise ValueError("row index must be positive")
    parts = []
    for number, symbol in ROMAN_TABLE:
        while value >= number:
            parts.append(symbol)
            value -= number
    return "".join(parts)


def default_thresholds():
    return {
        "occupiedMinMm": 300,
        "occupiedMaxMm": 1000,
        "freeMinMm": 1000,
        "faultMinMm": 10,
        "faultMaxMm": 300,
    }


def default_settings():
    return {
        "simulationMode": True,
        "autoPollEnabled": False,
        "pollIntervalSec": 10,
        "thresholds": default_thresholds(),
        "ui": {
            "showGridLabels": True,
            "compactMode": False,
        },
    }


def default_serial_profile():
    return {
        "id": "serial-default",
        "name": "ESP32 边缘 RS485 总线",
        "port": "edge-uart1",
        "baudrate": 115200,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "timeoutSec": 0.5,
        "enabled": True,
    }


def default_modbus_profile():
    return {
        "id": "modbus-default",
        "name": "超声波雷达实际寄存器模板",
        "addressRegister": 0x0015,
        "distanceRegister": 0x0010,
        "statusRegister": 0,
        "distanceRegisterCount": 1,
        "pollRegisterStart": 0x0010,
        "pollRegisterCount": 1,
        "onlineStatusValue": 1,
        "notes": "实际协议：115200 N 8 1；0x03 读取 0x0010 距离；0x06 写 0x0015 修改从机地址；默认地址 0x01，未知地址可用 0xFF 编址。",
    }


def make_slot(row_index, column_index):
    row_label = roman_label(row_index)
    column_label = excel_column_label(column_index)
    return {
        "id": "%s-%s" % (row_label, column_label),
        "rowIndex": row_index,
        "rowLabel": row_label,
        "columnIndex": column_index,
        "columnLabel": column_label,
        "enabled": False,
        "name": "",
        "notes": "",
        "tags": [],
        "radarId": "",
        "statusHint": "disabled",
    }


def build_layout(rows, columns):
    rows = max(1, min(32, int(rows)))
    columns = max(1, min(32, int(columns)))
    row_labels = [roman_label(index) for index in range(1, rows + 1)]
    column_labels = [excel_column_label(index) for index in range(1, columns + 1)]
    slots = []
    for row_index in range(1, rows + 1):
        for column_index in range(1, columns + 1):
            slots.append(make_slot(row_index, column_index))
    return {
        "rows": rows,
        "columns": columns,
        "rowLabels": row_labels,
        "columnLabels": column_labels,
        "slots": slots,
        "updatedAt": now_str(),
    }


def default_data():
    return {
        "layout": build_layout(4, 4),
        "settings": default_settings(),
        "radars": [],
        "edgeDevices": [],
        "edgeCommands": [],
        "serialProfiles": [default_serial_profile()],
        "modbusProfiles": [default_modbus_profile()],
        "operationHistory": [],
    }


def load_json(path, fallback):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
    except Exception as exc:
        logger.error("读取 JSON 失败 %s: %s", path, exc)
    return fallback


def save_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def load_logs():
    return load_json(LOG_FILE, [])


def save_logs(logs):
    save_json(LOG_FILE, logs)


def add_log(log_type, message, details=None):
    logs = load_logs()
    entry = {
        "type": log_type,
        "message": message,
        "time": now_str(),
    }
    if details is not None:
        entry["details"] = details
    logs.append(entry)
    save_logs(logs[-500:])
    logger.info("[%s] %s", log_type, message)
    return entry


def normalize_thresholds(thresholds):
    base = default_thresholds()
    thresholds = thresholds if isinstance(thresholds, dict) else {}
    for key, default in base.items():
        base[key] = coerce_int(thresholds.get(key), default)
    if base["occupiedMaxMm"] < base["occupiedMinMm"]:
        base["occupiedMaxMm"] = base["occupiedMinMm"]
    if base["freeMinMm"] < base["occupiedMaxMm"]:
        base["freeMinMm"] = base["occupiedMaxMm"]
    if base["faultMinMm"] < 0:
        base["faultMinMm"] = 0
    return base


def normalize_settings(settings):
    settings = settings if isinstance(settings, dict) else {}
    defaults = default_settings()
    ui = settings.get("ui") if isinstance(settings.get("ui"), dict) else {}
    defaults["simulationMode"] = bool_value(settings.get("simulationMode"), defaults["simulationMode"])
    defaults["autoPollEnabled"] = bool_value(settings.get("autoPollEnabled"), defaults["autoPollEnabled"])
    defaults["pollIntervalSec"] = max(2, coerce_int(settings.get("pollIntervalSec"), defaults["pollIntervalSec"]))
    defaults["thresholds"] = normalize_thresholds(settings.get("thresholds"))
    defaults["ui"]["showGridLabels"] = bool_value(ui.get("showGridLabels"), defaults["ui"]["showGridLabels"])
    defaults["ui"]["compactMode"] = bool_value(ui.get("compactMode"), defaults["ui"]["compactMode"])
    return defaults


def normalize_slot(slot, row_index=None, column_index=None):
    row_index = coerce_int(slot.get("rowIndex"), row_index or 1)
    column_index = coerce_int(slot.get("columnIndex"), column_index or 1)
    base = make_slot(row_index, column_index)
    base["enabled"] = bool_value(slot.get("enabled"), base["enabled"])
    base["name"] = str(slot.get("name") or "").strip()
    base["notes"] = str(slot.get("notes") or "").strip()
    base["tags"] = normalize_tags(slot.get("tags"))
    base["radarId"] = str(slot.get("radarId") or "").strip()
    base["statusHint"] = str(slot.get("statusHint") or base["statusHint"]).strip() or "disabled"
    return base


def normalize_layout(layout):
    layout = layout if isinstance(layout, dict) else {}
    rows = max(1, min(32, coerce_int(layout.get("rows"), 4)))
    columns = max(1, min(32, coerce_int(layout.get("columns"), 4)))
    rebuilt = build_layout(rows, columns)
    existing = {}
    for slot in layout.get("slots", []):
        if isinstance(slot, dict) and slot.get("id"):
            existing[str(slot.get("id"))] = slot
    slots = []
    for base in rebuilt["slots"]:
        current = existing.get(base["id"], {})
        merged = dict(base)
        merged.update(current)
        slots.append(normalize_slot(merged, row_index=base["rowIndex"], column_index=base["columnIndex"]))
    rebuilt["slots"] = slots
    rebuilt["updatedAt"] = str(layout.get("updatedAt") or now_str())
    return rebuilt


def normalize_serial_profile(profile):
    profile = profile if isinstance(profile, dict) else {}
    base = default_serial_profile()
    base["id"] = str(profile.get("id") or base["id"]).strip() or "serial-%s" % uuid.uuid4().hex[:8]
    base["name"] = str(profile.get("name") or base["name"]).strip() or "串口配置"
    base["port"] = str(profile.get("port") or base["port"]).strip() or "/dev/ttyUSB0"
    base["baudrate"] = max(1200, coerce_int(profile.get("baudrate"), base["baudrate"]))
    base["bytesize"] = max(5, min(8, coerce_int(profile.get("bytesize"), base["bytesize"])))
    base["parity"] = str(profile.get("parity") or base["parity"]).strip().upper() or "N"
    base["stopbits"] = 2 if str(profile.get("stopbits")) == "2" else 1
    base["timeoutSec"] = max(0.05, coerce_float(profile.get("timeoutSec"), base["timeoutSec"]))
    base["enabled"] = bool_value(profile.get("enabled"), True)
    return base


def normalize_modbus_profile(profile):
    profile = profile if isinstance(profile, dict) else {}
    base = default_modbus_profile()
    base["id"] = str(profile.get("id") or base["id"]).strip() or "modbus-%s" % uuid.uuid4().hex[:8]
    base["name"] = str(profile.get("name") or base["name"]).strip() or "寄存器模板"
    for key in ("addressRegister", "distanceRegister", "statusRegister", "distanceRegisterCount", "pollRegisterStart", "pollRegisterCount", "onlineStatusValue"):
        base[key] = max(0, coerce_int(profile.get(key), base[key]))
    base["notes"] = str(profile.get("notes") or base["notes"]).strip()
    return base


def normalize_radar(radar):
    radar = radar if isinstance(radar, dict) else {}
    radar_id = str(radar.get("id") or "radar-%s" % uuid.uuid4().hex[:10]).strip()
    return {
        "id": radar_id,
        "name": str(radar.get("name") or radar_id).strip(),
        "address": max(1, min(254, coerce_int(radar.get("address"), 1))),
        "slotId": str(radar.get("slotId") or "").strip(),
        "enabled": bool_value(radar.get("enabled"), True),
        "online": bool_value(radar.get("online"), False),
        "lastDistanceMm": coerce_int(radar.get("lastDistanceMm"), None),
        "lastStatusWord": coerce_int(radar.get("lastStatusWord"), None),
        "lastSeen": str(radar.get("lastSeen") or "").strip(),
        "lastPollTime": str(radar.get("lastPollTime") or "").strip(),
        "lastError": str(radar.get("lastError") or "").strip(),
        "source": str(radar.get("source") or "server").strip(),
        "edgeDeviceId": str(radar.get("edgeDeviceId") or "").strip(),
        "edgeIndex": coerce_int(radar.get("edgeIndex"), None),
        "notes": str(radar.get("notes") or "").strip(),
        "serialProfileId": str(radar.get("serialProfileId") or "serial-default").strip() or "serial-default",
        "modbusProfileId": str(radar.get("modbusProfileId") or "modbus-default").strip() or "modbus-default",
        "simulation": radar.get("simulation") if isinstance(radar.get("simulation"), dict) else {},
        "updatedAt": str(radar.get("updatedAt") or now_str()).strip(),
        "createdAt": str(radar.get("createdAt") or now_str()).strip(),
    }


def normalize_edge_device(device):
    device = device if isinstance(device, dict) else {}
    return {
        "id": str(device.get("id") or device.get("deviceId") or "").strip(),
        "name": str(device.get("name") or "ESP32 Edge Gateway").strip(),
        "mac": str(device.get("mac") or "").strip().upper(),
        "ip": str(device.get("ip") or "").strip(),
        "appVersion": str(device.get("appVersion") or "").strip(),
        "transport": str(device.get("transport") or "edge-esp32").strip(),
        "radarCount": coerce_int(device.get("radarCount"), 0),
        "occupiedCount": coerce_int(device.get("occupiedCount"), 0),
        "lastSeen": str(device.get("lastSeen") or "").strip(),
        "updatedAt": str(device.get("updatedAt") or now_str()).strip(),
    }


def normalize_edge_command(command):
    command = command if isinstance(command, dict) else {}
    command_id = str(command.get("id") or "cmd-%s" % uuid.uuid4().hex[:12]).strip()
    return {
        "id": command_id,
        "deviceId": str(command.get("deviceId") or "").strip(),
        "action": str(command.get("action") or "").strip(),
        "address": coerce_int(command.get("address"), None),
        "targetAddress": coerce_int(command.get("targetAddress"), None),
        "startRegister": coerce_int(command.get("startRegister"), None),
        "quantity": coerce_int(command.get("quantity"), None),
        "register": coerce_int(command.get("register"), None),
        "value": coerce_int(command.get("value"), None),
        "status": str(command.get("status") or "pending").strip(),
        "createdAt": str(command.get("createdAt") or now_str()).strip(),
        "deliveredAt": str(command.get("deliveredAt") or "").strip(),
        "completedAt": str(command.get("completedAt") or "").strip(),
        "result": command.get("result") if isinstance(command.get("result"), dict) else {},
        "message": str(command.get("message") or "").strip(),
    }


def normalize_data(data):
    data = data if isinstance(data, dict) else {}
    layout = normalize_layout(data.get("layout"))
    settings = normalize_settings(data.get("settings"))
    serial_profiles = [normalize_serial_profile(item) for item in data.get("serialProfiles", []) if isinstance(item, dict)]
    modbus_profiles = [normalize_modbus_profile(item) for item in data.get("modbusProfiles", []) if isinstance(item, dict)]
    if not serial_profiles:
        serial_profiles = [default_serial_profile()]
    if not modbus_profiles:
        modbus_profiles = [default_modbus_profile()]
    radars = [normalize_radar(item) for item in data.get("radars", []) if isinstance(item, dict)]
    edge_devices = [normalize_edge_device(item) for item in data.get("edgeDevices", []) if isinstance(item, dict)]
    edge_commands = [normalize_edge_command(item) for item in data.get("edgeCommands", []) if isinstance(item, dict)]
    history = data.get("operationHistory") if isinstance(data.get("operationHistory"), list) else []
    normalized = {
        "layout": layout,
        "settings": settings,
        "radars": radars,
        "edgeDevices": edge_devices,
        "edgeCommands": edge_commands[-200:],
        "serialProfiles": serial_profiles,
        "modbusProfiles": modbus_profiles,
        "operationHistory": history[-200:],
    }
    cleanup_radar_slot_links(normalized)
    return normalized


def load_data():
    return normalize_data(load_json(DATA_FILE, default_data()))


def save_data(data):
    save_json(DATA_FILE, normalize_data(data))


def reset_data_store():
    save_data(default_data())
    save_logs([])
    return load_data()


def configure_for_test(data_file=None, log_file=None, server_log_file=None, public_base_url=None, transport_factory=None):
    global DATA_FILE, LOG_FILE, SERVER_LOG_FILE, PUBLIC_BASE_URL, TRANSPORT_FACTORY
    if data_file:
        DATA_FILE = data_file
    if log_file:
        LOG_FILE = log_file
    if server_log_file:
        SERVER_LOG_FILE = server_log_file
    if public_base_url:
        PUBLIC_BASE_URL = public_base_url.rstrip("/")
    if transport_factory is not None:
        TRANSPORT_FACTORY = transport_factory
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)


def server_modbus_enabled():
    return SERVER_MODBUS_ENABLED or TRANSPORT_FACTORY is not None


def server_modbus_disabled_response():
    return jsonify({
        "success": False,
        "message": "8081 服务器不再直连 /dev/ttyUSB0。雷达实际挂在 ESP32 边缘侧，请通过 /api/edge/devices/<deviceId>/commands 下发 read_distance/read_registers/write_register/change_address。",
    }), 409


def push_operation_history(data, action, payload):
    history = data.get("operationHistory", [])
    history.append({
        "time": now_str(),
        "action": action,
        "payload": payload,
    })
    data["operationHistory"] = history[-200:]


def serial_profile_index(data):
    return {item["id"]: item for item in data.get("serialProfiles", [])}


def modbus_profile_index(data):
    return {item["id"]: item for item in data.get("modbusProfiles", [])}


def radar_index(data):
    return {item["id"]: item for item in data.get("radars", [])}


def slot_index(data):
    return {item["id"]: item for item in data.get("layout", {}).get("slots", [])}


def get_slot(data, slot_id):
    return slot_index(data).get(slot_id)


def get_radar(data, radar_id):
    return radar_index(data).get(radar_id)


def get_default_serial_profile(data):
    return data.get("serialProfiles", [default_serial_profile()])[0]


def get_default_modbus_profile(data):
    return data.get("modbusProfiles", [default_modbus_profile()])[0]


def cleanup_radar_slot_links(data):
    slots = data.get("layout", {}).get("slots", [])
    slot_ids = {slot["id"] for slot in slots}
    radars = data.get("radars", [])
    radars_by_id = {radar["id"]: radar for radar in radars}
    for slot in slots:
        radar_id = slot.get("radarId")
        if radar_id and radar_id not in radars_by_id:
            slot["radarId"] = ""
    for radar in radars:
        if radar.get("slotId") and radar["slotId"] not in slot_ids:
            radar["slotId"] = ""
    for slot in slots:
        radar = radars_by_id.get(slot.get("radarId"))
        if radar:
            radar["slotId"] = slot["id"]
    for radar in radars:
        if radar.get("slotId"):
            slot = next((item for item in slots if item["id"] == radar["slotId"]), None)
            if slot:
                slot["radarId"] = radar["id"]


def resize_layout(data, rows, columns):
    current_slots = {slot["id"]: slot for slot in data["layout"]["slots"]}
    rebuilt = build_layout(rows, columns)
    merged_slots = []
    for slot in rebuilt["slots"]:
        previous = current_slots.get(slot["id"])
        if previous:
            preserved = dict(slot)
            preserved.update(previous)
            merged_slots.append(normalize_slot(preserved, row_index=slot["rowIndex"], column_index=slot["columnIndex"]))
        else:
            merged_slots.append(slot)
    rebuilt["slots"] = merged_slots
    data["layout"] = rebuilt
    cleanup_radar_slot_links(data)
    push_operation_history(data, "layout.resize", {"rows": rows, "columns": columns})
    add_log("success", "停车位布局已调整为 %s x %s" % (rows, columns))
    save_data(data)
    return data["layout"]


def ensure_radar(data, payload):
    payload = payload if isinstance(payload, dict) else {}
    radar_id = str(payload.get("id") or "").strip()
    radar = get_radar(data, radar_id) if radar_id else None
    if radar is None:
        radar = normalize_radar(payload)
        data["radars"].append(radar)
    else:
        radar.update(normalize_radar(dict(radar, **payload)))
    cleanup_radar_slot_links(data)
    return radar


def bind_slot(data, slot_id, payload):
    slot = get_slot(data, slot_id)
    if slot is None:
        raise KeyError("slot not found")
    payload = payload if isinstance(payload, dict) else {}
    slot["enabled"] = bool_value(payload.get("enabled"), slot.get("enabled", False))
    slot["name"] = str(payload.get("name") or slot.get("name") or "").strip()
    slot["notes"] = str(payload.get("notes") or slot.get("notes") or "").strip()
    slot["tags"] = normalize_tags(payload.get("tags", slot.get("tags", [])))

    new_radar_id = str(payload.get("radarId") or slot.get("radarId") or "").strip()
    if "radarId" in payload:
        old_radar_id = slot.get("radarId")
        if old_radar_id and old_radar_id != new_radar_id:
            old_radar = get_radar(data, old_radar_id)
            if old_radar:
                old_radar["slotId"] = ""
        if new_radar_id:
            for other_slot in data.get("layout", {}).get("slots", []):
                if other_slot.get("id") != slot_id and other_slot.get("radarId") == new_radar_id:
                    other_slot["radarId"] = ""
                    other_slot["statusHint"] = "enabled" if other_slot.get("enabled") else "disabled"
        slot["radarId"] = new_radar_id

    if not slot["enabled"]:
        slot["statusHint"] = "disabled"
    elif slot.get("radarId"):
        slot["statusHint"] = "bound"
    else:
        slot["statusHint"] = "enabled"

    radar = None
    if slot.get("radarId"):
        radar = get_radar(data, slot["radarId"])
        if radar is None:
            raise KeyError("radar not found")
        radar["slotId"] = slot["id"]
    cleanup_radar_slot_links(data)
    push_operation_history(data, "slot.bind", {"slotId": slot_id, "radarId": slot.get("radarId"), "enabled": slot["enabled"]})
    add_log("success", "车位 %s 已更新绑定" % slot_id)
    save_data(data)
    return slot, radar


def derive_slot_state(slot, radar, settings):
    thresholds = settings.get("thresholds", default_thresholds())
    palette = {
        "disabled": {"label": "未启用", "color": "#b7bec9", "emphasis": "muted"},
        "enabled": {"label": "已启用待绑定", "color": "#8b95a7", "emphasis": "soft"},
        "bound": {"label": "已绑定待轮询", "color": "#4f7cff", "emphasis": "medium"},
        "fault": {"label": "异常", "color": "#ff6b6b", "emphasis": "strong"},
        "occupied": {"label": "有车占用", "color": "#ff9f43", "emphasis": "strong"},
        "free": {"label": "空闲", "color": "#2ecc71", "emphasis": "strong"},
    }

    if not slot.get("enabled"):
        code = "disabled"
        detail = "该车位未启用"
    elif not radar:
        code = "enabled"
        detail = "已启用，但尚未绑定雷达地址"
    elif not radar.get("online"):
        code = "fault"
        detail = radar.get("lastError") or "雷达不在线"
    else:
        distance = coerce_int(radar.get("lastDistanceMm"), None)
        if distance is None or (isinstance(distance, float) and math.isnan(distance)):
            code = "bound"
            detail = "雷达已绑定，等待有效测距"
        elif distance < thresholds.get("faultMinMm", 30):
            code = "fault"
            detail = "距离 %s mm，低于异常阈值" % distance
        elif distance < thresholds.get("faultMaxMm", thresholds.get("occupiedMinMm", 30)):
            code = "fault"
            detail = "距离 %s mm，处于异常区间" % distance
        elif thresholds.get("occupiedMinMm", 30) <= distance <= thresholds.get("occupiedMaxMm", 100):
            code = "occupied"
            detail = "距离 %s mm，判定为有车" % distance
        elif distance >= thresholds.get("freeMinMm", 100):
            code = "free"
            detail = "距离 %s mm，判定为空闲" % distance
        else:
            code = "bound"
            detail = "距离 %s mm，位于占用/空闲阈值之间，等待人工确认" % distance

    state = dict(palette[code])
    state["code"] = code
    state["detail"] = detail
    return state


def snapshot_map(data):
    radars = [dict(item) for item in data.get("radars", [])]
    radars_by_id = {item["id"]: item for item in radars}
    slots = []
    for slot in data.get("layout", {}).get("slots", []):
        current = dict(slot)
        radar = radars_by_id.get(slot.get("radarId"))
        current["radar"] = dict(radar) if radar else None
        current["derivedState"] = derive_slot_state(slot, radar, data.get("settings", {}))
        slots.append(current)
    return {
        "layout": data.get("layout", {}),
        "slots": slots,
        "radars": radars,
        "settings": data.get("settings", {}),
        "serialProfiles": data.get("serialProfiles", []),
        "modbusProfiles": data.get("modbusProfiles", []),
        "edgeDevices": data.get("edgeDevices", []),
        "edgeCommands": data.get("edgeCommands", [])[-30:],
        "operationHistory": data.get("operationHistory", [])[-30:],
    }


class SimulationTransport:
    def read_holding_registers(self, address, start_register, quantity, serial_profile, modbus_profile):
        data = load_data()
        radar = next((item for item in data.get("radars", []) if item.get("address") == address), None)
        if not radar:
            raise RuntimeError("模拟总线中不存在地址 %s" % address)
        simulation = radar.get("simulation") if isinstance(radar.get("simulation"), dict) else {}
        distance = coerce_int(simulation.get("distanceMm", radar.get("lastDistanceMm")), None)
        status_word = coerce_int(simulation.get("statusWord", radar.get("lastStatusWord", 1)), 1)
        if not bool_value(simulation.get("online", radar.get("online")), radar.get("online", False)):
            raise RuntimeError("模拟雷达 %s 不在线" % address)
        values = []
        if quantity == 1 and start_register == modbus_profile.get("distanceRegister", 2):
            values = [distance if distance is not None else 0]
        elif quantity == 1 and start_register == modbus_profile.get("statusRegister", 3):
            values = [status_word]
        elif quantity == modbus_profile.get("pollRegisterCount", 2) and start_register == modbus_profile.get("pollRegisterStart", 2):
            values = [distance if distance is not None else 0, status_word]
        else:
            values = [0 for _ in range(quantity)]
        return values

    def write_single_register(self, address, register, value, serial_profile, modbus_profile):
        data = load_data()
        radar = next((item for item in data.get("radars", []) if item.get("address") == address), None)
        if radar is None:
            radar = normalize_radar({"name": "Radar %s" % address, "address": address})
            data["radars"].append(radar)
        if register == modbus_profile.get("addressRegister", 1):
            radar["address"] = value
            radar["updatedAt"] = now_str()
            save_data(data)
        return {"address": address, "register": register, "value": value}


class SerialModbusTransport:
    def __init__(self):
        if serial is None:
            raise RuntimeError("当前环境未安装 pyserial，无法启用真实串口 Modbus")

    @staticmethod
    def crc16(data_bytes):
        crc = 0xFFFF
        for value in data_bytes:
            crc ^= value
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    def transact(self, frame, expected_length, serial_profile):
        ser = serial.Serial(
            port=serial_profile["port"],
            baudrate=serial_profile["baudrate"],
            bytesize=serial_profile["bytesize"],
            parity=serial_profile["parity"],
            stopbits=serial_profile["stopbits"],
            timeout=serial_profile["timeoutSec"],
        )
        try:
            ser.reset_input_buffer()
            ser.write(frame)
            ser.flush()
            response = ser.read(expected_length)
        finally:
            ser.close()
        if len(response) != expected_length:
            raise RuntimeError("Modbus 响应长度异常：期望 %s 实际 %s" % (expected_length, len(response)))
        return response

    def read_holding_registers(self, address, start_register, quantity, serial_profile, modbus_profile):
        frame = bytearray([
            address,
            0x03,
            (start_register >> 8) & 0xFF,
            start_register & 0xFF,
            (quantity >> 8) & 0xFF,
            quantity & 0xFF,
        ])
        crc = self.crc16(frame)
        frame.extend([crc & 0xFF, (crc >> 8) & 0xFF])
        response = self.transact(bytes(frame), 5 + quantity * 2, serial_profile)
        if response[0] != address or response[1] != 0x03:
            raise RuntimeError("Modbus 读寄存器响应帧非法")
        byte_count = response[2]
        if byte_count != quantity * 2:
            raise RuntimeError("Modbus 字节数异常")
        registers = []
        for index in range(quantity):
            offset = 3 + index * 2
            registers.append((response[offset] << 8) | response[offset + 1])
        return registers

    def write_single_register(self, address, register, value, serial_profile, modbus_profile):
        frame = bytearray([
            address,
            0x06,
            (register >> 8) & 0xFF,
            register & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ])
        crc = self.crc16(frame)
        frame.extend([crc & 0xFF, (crc >> 8) & 0xFF])
        response = self.transact(bytes(frame), 8, serial_profile)
        if response[:6] != bytes(frame[:6]):
            raise RuntimeError("Modbus 写单寄存器响应帧非法")
        return {"address": address, "register": register, "value": value}


def get_transport(data):
    if TRANSPORT_FACTORY is not None:
        return TRANSPORT_FACTORY()
    if data.get("settings", {}).get("simulationMode", True):
        return SimulationTransport()
    if not SERVER_MODBUS_ENABLED:
        raise RuntimeError("服务器直连 Modbus 已禁用；请通过 ESP32 边缘网关下行命令访问雷达")
    return SerialModbusTransport()


def poll_radar(data, radar):
    serial_profiles = serial_profile_index(data)
    modbus_profiles = modbus_profile_index(data)
    serial_profile = serial_profiles.get(radar.get("serialProfileId")) or get_default_serial_profile(data)
    modbus_profile = modbus_profiles.get(radar.get("modbusProfileId")) or get_default_modbus_profile(data)
    transport = get_transport(data)
    start_register = modbus_profile.get("pollRegisterStart", 2)
    register_count = max(1, modbus_profile.get("pollRegisterCount", 2))
    registers = transport.read_holding_registers(radar["address"], start_register, register_count, serial_profile, modbus_profile)
    distance = registers[0] if registers else None
    status_word = registers[1] if len(registers) > 1 else None
    radar["lastDistanceMm"] = coerce_int(distance, radar.get("lastDistanceMm"))
    radar["lastStatusWord"] = coerce_int(status_word, radar.get("lastStatusWord"))
    radar["lastPollTime"] = now_str()
    radar["lastSeen"] = now_str()
    radar["online"] = True
    radar["lastError"] = ""
    radar["updatedAt"] = now_str()
    return {
        "radarId": radar["id"],
        "address": radar["address"],
        "distanceMm": radar.get("lastDistanceMm"),
        "statusWord": radar.get("lastStatusWord"),
        "online": True,
    }


def poll_all_bound_radars(data):
    results = []
    for radar in data.get("radars", []):
        if not radar.get("enabled") or not radar.get("slotId"):
            continue
        try:
            results.append(poll_radar(data, radar))
        except Exception as exc:
            radar["online"] = False
            radar["lastPollTime"] = now_str()
            radar["lastError"] = str(exc)
            results.append({
                "radarId": radar["id"],
                "address": radar["address"],
                "online": False,
                "error": str(exc),
            })
    push_operation_history(data, "modbus.poll_all", {"count": len(results)})
    save_data(data)
    return results


def discover_radars(data, start_address, end_address):
    start_address = max(1, min(247, int(start_address)))
    end_address = max(start_address, min(247, int(end_address)))
    serial_profiles = serial_profile_index(data)
    modbus_profiles = modbus_profile_index(data)
    default_serial = get_default_serial_profile(data)
    default_modbus = get_default_modbus_profile(data)
    transport = get_transport(data)
    found = []
    for address in range(start_address, end_address + 1):
        try:
            serial_profile = serial_profiles.get(default_serial["id"], default_serial)
            modbus_profile = modbus_profiles.get(default_modbus["id"], default_modbus)
            values = transport.read_holding_registers(
                address,
                modbus_profile.get("pollRegisterStart", 2),
                max(1, modbus_profile.get("pollRegisterCount", 2)),
                serial_profile,
                modbus_profile,
            )
            distance = values[0] if values else None
            status_word = values[1] if len(values) > 1 else None
            radar = next((item for item in data["radars"] if item.get("address") == address), None)
            if radar is None:
                radar = normalize_radar({
                    "name": "Radar %s" % address,
                    "address": address,
                    "serialProfileId": serial_profile["id"],
                    "modbusProfileId": modbus_profile["id"],
                })
                data["radars"].append(radar)
            radar["online"] = True
            radar["lastDistanceMm"] = coerce_int(distance, radar.get("lastDistanceMm"))
            radar["lastStatusWord"] = coerce_int(status_word, radar.get("lastStatusWord"))
            radar["lastSeen"] = now_str()
            radar["lastPollTime"] = now_str()
            radar["lastError"] = ""
            found.append(dict(radar))
        except Exception:
            continue
    push_operation_history(data, "modbus.discover", {"startAddress": start_address, "endAddress": end_address, "found": len(found)})
    add_log("info", "地址扫描完成：%s-%s，发现 %s 个雷达" % (start_address, end_address, len(found)))
    save_data(data)
    return found


def program_radar_address(data, source_address, target_address, slot_id=None, radar_name=None):
    source_address = coerce_int(source_address, None)
    target_address = coerce_int(target_address, None)
    if source_address is None or not (1 <= source_address <= 255):
        raise ValueError("源地址范围必须为 1-255，其中 255 可用于未知地址编址")
    if target_address is None or not (1 <= target_address <= 254):
        raise ValueError("目标地址范围必须为 1-254")
    if source_address == target_address:
        raise ValueError("源地址与目标地址不能相同")
    serial_profile = get_default_serial_profile(data)
    modbus_profile = get_default_modbus_profile(data)
    transport = get_transport(data)
    transport.write_single_register(source_address, modbus_profile.get("addressRegister", 1), target_address, serial_profile, modbus_profile)
    radar = next((item for item in data["radars"] if item.get("address") == source_address), None)
    if radar is None:
        radar = normalize_radar({
            "name": radar_name or "Radar %s" % target_address,
            "address": target_address,
            "serialProfileId": serial_profile["id"],
            "modbusProfileId": modbus_profile["id"],
        })
        data["radars"].append(radar)
    radar["address"] = target_address
    radar["name"] = str(radar_name or radar.get("name") or ("Radar %s" % target_address)).strip()
    radar["online"] = True
    radar["lastSeen"] = now_str()
    radar["lastPollTime"] = now_str()
    radar["lastError"] = ""
    if slot_id:
        slot = get_slot(data, slot_id)
        if slot is None:
            raise KeyError("slot not found")
        slot["enabled"] = True
        slot["radarId"] = radar["id"]
        slot["statusHint"] = "bound"
        radar["slotId"] = slot_id
    cleanup_radar_slot_links(data)
    push_operation_history(data, "modbus.address_program", {"sourceAddress": source_address, "targetAddress": target_address, "slotId": slot_id or ""})
    add_log("success", "雷达地址编程成功：%s -> %s" % (source_address, target_address))
    save_data(data)
    slot = get_slot(data, slot_id) if slot_id else None
    return radar, slot


def first_slot_for_edge_index(data, edge_index):
    slots = data.get("layout", {}).get("slots", [])
    if 0 <= edge_index < len(slots):
        return slots[edge_index]
    return None


def release_legacy_server_radar_slot(data, edge_radar):
    """当边缘雷达心跳到来时，将同地址的旧 server 雷达从车位中迁出，由边缘雷达接管绑定。"""
    address = edge_radar.get("address")
    for legacy in data.get("radars", []):
        if legacy.get("id") == edge_radar.get("id"):
            continue
        if legacy.get("source") == "edge":
            continue
        if legacy.get("address") != address:
            continue
        slot_id = legacy.get("slotId")
        if not slot_id:
            continue
        slot = next((item for item in data.get("layout", {}).get("slots", [])
                     if item.get("id") == slot_id), None)
        if slot and slot.get("radarId") == legacy.get("id"):
            slot["radarId"] = edge_radar["id"]
            slot["enabled"] = True
            slot["statusHint"] = "bound"
            edge_radar["slotId"] = slot["id"]
        legacy["slotId"] = ""
        legacy["online"] = False
        legacy["lastError"] = "已由 ESP32 边缘雷达记录接管"
        legacy["updatedAt"] = now_str()
        return slot
    return None


def upsert_edge_device(data, payload):
    payload = payload if isinstance(payload, dict) else {}
    device_id = str(payload.get("deviceId") or payload.get("id") or payload.get("mac") or "").strip()
    if not device_id:
        raise ValueError("deviceId required")
    device_payload = normalize_edge_device({
        "id": device_id,
        "name": payload.get("name"),
        "mac": payload.get("mac"),
        "ip": payload.get("ip"),
        "appVersion": payload.get("appVersion"),
        "transport": payload.get("transport"),
        "radarCount": payload.get("radarCount"),
        "occupiedCount": payload.get("occupiedCount"),
        "lastSeen": now_str(),
        "updatedAt": now_str(),
    })
    devices = data.setdefault("edgeDevices", [])
    existing = next((item for item in devices if item.get("id") == device_id), None)
    if existing is None:
        devices.append(device_payload)
    else:
        existing.clear()
        existing.update(device_payload)
    return device_payload


def apply_edge_radar_snapshot(data, device_id, radar_payloads):
    radar_payloads = radar_payloads if isinstance(radar_payloads, list) else []
    serial_profile = get_default_serial_profile(data)
    modbus_profile = get_default_modbus_profile(data)
    touched = []

    for index, item in enumerate(radar_payloads):
        if not isinstance(item, dict):
            continue
        address = coerce_int(item.get("address"), None)
        if address is None or not (1 <= address <= 254):
            continue
        radar_id = str(item.get("id") or "%s-radar-%02d" % (device_id, address)).strip()
        radar = get_radar(data, radar_id)
        payload = {
            "id": radar_id,
            "name": str(item.get("name") or "Edge Radar %02d" % address),
            "address": address,
            "enabled": True,
            "online": bool_value(item.get("online"), False),
            "lastDistanceMm": coerce_int(item.get("distanceMm"), None),
            "lastStatusWord": coerce_int(item.get("statusWord"), 1),
            "lastSeen": now_str() if bool_value(item.get("online"), False) else (radar or {}).get("lastSeen", ""),
            "lastPollTime": now_str(),
            "lastError": "" if bool_value(item.get("online"), False) else str(item.get("status") or "edge offline"),
            "source": "edge",
            "edgeDeviceId": device_id,
            "edgeIndex": coerce_int(item.get("queueIndex"), index),
            "serialProfileId": serial_profile["id"],
            "modbusProfileId": modbus_profile["id"],
            "updatedAt": now_str(),
        }
        if radar is None:
            radar = normalize_radar(payload)
            data["radars"].append(radar)
        else:
            old_slot = radar.get("slotId")
            radar.update(normalize_radar(dict(radar, **payload)))
            radar["slotId"] = old_slot

        if not radar.get("slotId"):
            release_legacy_server_radar_slot(data, radar)
        if not radar.get("slotId"):
            slot = first_slot_for_edge_index(data, index)
            if slot and (not slot.get("radarId") or slot.get("radarId") == radar["id"]):
                slot["enabled"] = True
                slot["radarId"] = radar["id"]
                slot["statusHint"] = "bound"
                radar["slotId"] = slot["id"]
        touched.append(dict(radar))

    cleanup_radar_slot_links(data)
    return touched


def handle_edge_heartbeat(payload):
    data = load_data()
    device = upsert_edge_device(data, payload)
    radars = apply_edge_radar_snapshot(data, device["id"], payload.get("radars", []))
    push_operation_history(data, "edge.heartbeat", {
        "deviceId": device["id"],
        "radarCount": len(radars),
        "occupiedCount": device.get("occupiedCount", 0),
    })
    save_data(data)
    return device, radars


def create_edge_command(data, device_id, payload):
    payload = payload if isinstance(payload, dict) else {}
    command = normalize_edge_command(dict(payload, deviceId=device_id, status="pending", createdAt=now_str()))
    if not command["action"]:
        raise ValueError("action required")
    if command["address"] is None:
        raise ValueError("address required")
    data.setdefault("edgeCommands", []).append(command)
    push_operation_history(data, "edge.command.create", {
        "deviceId": device_id,
        "commandId": command["id"],
        "action": command["action"],
        "address": command["address"],
    })
    save_data(data)
    return command


def pending_edge_commands(data, device_id, limit=1):
    commands = []
    for command in data.get("edgeCommands", []):
        if command.get("deviceId") == device_id and command.get("status") == "pending":
            command["status"] = "delivered"
            command["deliveredAt"] = now_str()
            commands.append(command)
            if len(commands) >= limit:
                break
    if commands:
        save_data(data)
    return commands


def complete_edge_command(data, device_id, command_id, payload):
    payload = payload if isinstance(payload, dict) else {}
    command = next((item for item in data.get("edgeCommands", [])
                    if item.get("deviceId") == device_id and item.get("id") == command_id), None)
    if command is None:
        raise KeyError("command not found")
    success = bool_value(payload.get("success"), False)
    command["status"] = "success" if success else "failed"
    command["completedAt"] = now_str()
    command["result"] = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    command["message"] = str(payload.get("message") or "").strip()
    push_operation_history(data, "edge.command.result", {
        "deviceId": device_id,
        "commandId": command_id,
        "status": command["status"],
    })
    save_data(data)
    return command


def get_active_edge_device_id(data, preferred_device_id=None):
    """返回最合适的边缘设备 ID（优先指定，否则取最近在线设备）。"""
    devices = data.get("edgeDevices", [])
    if preferred_device_id:
        if any(d.get("id") == preferred_device_id for d in devices):
            return preferred_device_id
    online = [d for d in devices if d.get("online")]
    if online:
        online.sort(key=lambda d: d.get("lastSeen") or "", reverse=True)
        return online[0]["id"]
    if devices:
        return devices[0]["id"]
    return None


def execute_via_edge(device_id, action, command_payload, timeout=22, poll_interval=1.5):
    """透明桥接：将 Modbus 操作下发到 ESP32 边缘设备，同步等待结果。

    工作流程：
      1. 创建 pending 边缘命令写入数据文件
      2. ESP32 每 5s 轮询 /api/edge/devices/<id>/commands 取走命令
      3. ESP32 执行完成后回调 /api/edge/devices/<id>/commands/<cmd_id>/complete
      4. 本函数轮询数据文件直到命令状态变为 success/failed

    返回 (result_dict, error_str)，成功时 error_str 为 None。
    """
    data = load_data()
    cmd_dict = dict(command_payload, deviceId=device_id, action=action, status="pending", createdAt=now_str())
    command = normalize_edge_command(cmd_dict)
    data.setdefault("edgeCommands", []).append(command)
    push_operation_history(data, "edge.command.create", {
        "deviceId": device_id,
        "commandId": command["id"],
        "action": action,
    })
    save_data(data)
    command_id = command["id"]
    logger.info("edge bridge: dispatched %s cmd=%s device=%s", action, command_id, device_id)

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        fresh = load_data()
        cmd = next((c for c in fresh.get("edgeCommands", []) if c["id"] == command_id), None)
        if cmd is None:
            return None, "边缘命令记录丢失"
        if cmd["status"] == "success":
            logger.info("edge bridge: success cmd=%s", command_id)
            return cmd.get("result") or {}, None
        if cmd["status"] == "failed":
            logger.info("edge bridge: failed cmd=%s msg=%s", command_id, cmd.get("message"))
            return None, cmd.get("message") or "边缘设备执行失败"
    # 超时：将命令标记为 failed
    fresh = load_data()
    cmd = next((c for c in fresh.get("edgeCommands", []) if c["id"] == command_id), None)
    if cmd and cmd["status"] in ("pending", "delivered"):
        cmd["status"] = "failed"
        cmd["completedAt"] = now_str()
        cmd["message"] = "云端等待超时（%ds），ESP32 每 5s 轮询一次命令" % timeout
        save_data(fresh)
    logger.warning("edge bridge: timeout cmd=%s device=%s", command_id, device_id)
    return None, "等待边缘设备响应超时（%ds）。ESP32 每 5s 轮询一次，请确认设备在线后重试。" % timeout


def update_simulation(data, radar_id, payload):
    radar = get_radar(data, radar_id)
    if radar is None:
        raise KeyError("radar not found")
    payload = payload if isinstance(payload, dict) else {}
    simulation = radar.get("simulation") if isinstance(radar.get("simulation"), dict) else {}
    if "distanceMm" in payload:
        simulation["distanceMm"] = coerce_int(payload.get("distanceMm"), simulation.get("distanceMm"))
        radar["lastDistanceMm"] = simulation["distanceMm"]
    if "statusWord" in payload:
        simulation["statusWord"] = coerce_int(payload.get("statusWord"), simulation.get("statusWord", 1))
        radar["lastStatusWord"] = simulation["statusWord"]
    if "online" in payload:
        simulation["online"] = bool_value(payload.get("online"), True)
        radar["online"] = simulation["online"]
        if radar["online"]:
            radar["lastSeen"] = now_str()
        else:
            radar["lastError"] = "模拟为离线状态"
    radar["simulation"] = simulation
    radar["updatedAt"] = now_str()
    save_data(data)
    return radar


def poll_loop():  # pragma: no cover - background service path
    while not POLL_STOP_EVENT.is_set():
        try:
            data = load_data()
            if data.get("settings", {}).get("autoPollEnabled") and server_modbus_enabled():
                with POLL_LOCK:
                    poll_all_bound_radars(data)
        except Exception as exc:
            logger.error("后台轮询失败：%s", exc)
        wait_seconds = max(2, load_data().get("settings", {}).get("pollIntervalSec", 10))
        POLL_STOP_EVENT.wait(wait_seconds)


def start_background_poller():  # pragma: no cover - background service path
    global POLL_THREAD
    if POLL_THREAD and POLL_THREAD.is_alive():
        return
    POLL_STOP_EVENT.clear()
    POLL_THREAD = threading.Thread(target=poll_loop, name="radar-platform-poller", daemon=True)
    POLL_THREAD.start()


@app.route("/health", methods=["GET"])
def health_check():
    data = load_data()
    return jsonify({
        "status": "ok",
        "message": "Radar binding platform is running",
        "radarCount": len(data.get("radars", [])),
        "slotCount": len(data.get("layout", {}).get("slots", [])),
    })


@app.route("/api/dashboard", methods=["GET"])
def get_dashboard():
    return jsonify(snapshot_map(load_data()))


@app.route("/api/map", methods=["GET"])
def get_map():
    return jsonify(snapshot_map(load_data()))


@app.route("/api/edge/heartbeat", methods=["POST"])
def edge_heartbeat():
    payload = request.get_json(silent=True) or {}
    try:
        device, radars = handle_edge_heartbeat(payload)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    add_log("info", "边缘网关心跳：%s，%s 个雷达" % (device["id"], len(radars)))
    return jsonify({"success": True, "data": {"device": device, "radars": radars}})


@app.route("/api/edge/devices", methods=["GET"])
def get_edge_devices():
    return jsonify(load_data().get("edgeDevices", []))


@app.route("/api/edge/devices/<device_id>/commands", methods=["GET"])
def get_edge_device_commands(device_id):
    data = load_data()
    limit = max(1, min(8, coerce_int(request.args.get("limit"), 1)))
    commands = pending_edge_commands(data, device_id, limit=limit)
    return jsonify({"success": True, "commands": commands})


@app.route("/api/edge/devices/<device_id>/commands", methods=["POST"])
def post_edge_device_command(device_id):
    data = load_data()
    try:
        command = create_edge_command(data, device_id, request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    add_log("success", "已下发边缘命令：%s %s 地址 %s" % (command["action"], device_id, command.get("address")))
    return jsonify({"success": True, "data": command})


@app.route("/api/edge/devices/<device_id>/commands/<command_id>/result", methods=["POST"])
def post_edge_device_command_result(device_id, command_id):
    data = load_data()
    try:
        command = complete_edge_command(data, device_id, command_id, request.get_json(silent=True) or {})
    except KeyError as exc:
        return jsonify({"success": False, "message": str(exc)}), 404
    add_log("info", "边缘命令结果：%s %s" % (command_id, command["status"]))
    return jsonify({"success": True, "data": command})


@app.route("/api/layout", methods=["GET"])
def get_layout():
    return jsonify(load_data().get("layout", {}))


@app.route("/api/layout", methods=["PUT"])
def update_layout():
    data = load_data()
    payload = request.get_json(silent=True) or {}
    rows = coerce_int(payload.get("rows"), data["layout"]["rows"])
    columns = coerce_int(payload.get("columns"), data["layout"]["columns"])
    layout = resize_layout(data, rows, columns)
    return jsonify({"success": True, "data": layout})


@app.route("/api/settings", methods=["GET"])
def get_settings():
    data = load_data()
    return jsonify(data.get("settings", {}))


@app.route("/api/settings", methods=["PUT"])
def update_settings():
    data = load_data()
    payload = request.get_json(silent=True) or {}
    merged = dict(data.get("settings", {}))
    merged.update({key: value for key, value in payload.items() if key != "thresholds" and key != "ui"})
    if "thresholds" in payload:
        merged["thresholds"] = payload.get("thresholds")
    if "ui" in payload:
        merged["ui"] = payload.get("ui")
    data["settings"] = normalize_settings(merged)
    push_operation_history(data, "settings.update", data["settings"])
    add_log("success", "平台设置已更新")
    save_data(data)
    return jsonify({"success": True, "data": data["settings"]})


@app.route("/api/radars", methods=["GET"])
def get_radars():
    return jsonify(load_data().get("radars", []))


@app.route("/api/radars", methods=["POST"])
def create_radar():
    data = load_data()
    payload = request.get_json(silent=True) or {}
    radar = ensure_radar(data, payload)
    push_operation_history(data, "radar.create_or_update", {"radarId": radar["id"], "address": radar["address"]})
    add_log("success", "雷达记录已保存：%s (地址 %s)" % (radar["name"], radar["address"]))
    save_data(data)
    return jsonify({"success": True, "data": radar})


@app.route("/api/radars/<radar_id>", methods=["PUT"])
def update_radar(radar_id):
    data = load_data()
    radar = get_radar(data, radar_id)
    if radar is None:
        return jsonify({"success": False, "message": "radar not found"}), 404
    payload = request.get_json(silent=True) or {}
    payload["id"] = radar_id
    updated = ensure_radar(data, dict(radar, **payload))
    push_operation_history(data, "radar.update", {"radarId": radar_id})
    add_log("success", "雷达已更新：%s" % radar_id)
    save_data(data)
    return jsonify({"success": True, "data": updated})


@app.route("/api/radars/<radar_id>", methods=["DELETE"])
def delete_radar(radar_id):
    data = load_data()
    radar = get_radar(data, radar_id)
    if radar is None:
        return jsonify({"success": False, "message": "radar not found"}), 404
    data["radars"] = [item for item in data["radars"] if item.get("id") != radar_id]
    for slot in data["layout"]["slots"]:
        if slot.get("radarId") == radar_id:
            slot["radarId"] = ""
            slot["statusHint"] = "enabled" if slot.get("enabled") else "disabled"
    cleanup_radar_slot_links(data)
    push_operation_history(data, "radar.delete", {"radarId": radar_id})
    add_log("info", "雷达已删除：%s" % radar_id)
    save_data(data)
    return jsonify({"success": True})


@app.route("/api/radars/<radar_id>/simulation", methods=["POST"])
def update_radar_simulation(radar_id):
    data = load_data()
    try:
        radar = update_simulation(data, radar_id, request.get_json(silent=True) or {})
    except KeyError:
        return jsonify({"success": False, "message": "radar not found"}), 404
    add_log("info", "雷达模拟值已更新：%s" % radar_id)
    return jsonify({"success": True, "data": radar})


@app.route("/api/slots/<slot_id>", methods=["PUT"])
def update_slot(slot_id):
    data = load_data()
    try:
        slot, radar = bind_slot(data, slot_id, request.get_json(silent=True) or {})
    except KeyError as exc:
        return jsonify({"success": False, "message": str(exc)}), 404
    return jsonify({"success": True, "data": {"slot": slot, "radar": radar}})


@app.route("/api/serial-profiles", methods=["GET"])
def get_serial_profiles():
    return jsonify(load_data().get("serialProfiles", []))


@app.route("/api/serial-profiles", methods=["PUT"])
def replace_serial_profiles():
    data = load_data()
    payload = request.get_json(silent=True) or {}
    profiles = payload if isinstance(payload, list) else payload.get("profiles", [])
    data["serialProfiles"] = [normalize_serial_profile(item) for item in profiles if isinstance(item, dict)] or [default_serial_profile()]
    push_operation_history(data, "serial_profiles.replace", {"count": len(data['serialProfiles'])})
    add_log("success", "串口配置已更新")
    save_data(data)
    return jsonify({"success": True, "data": data["serialProfiles"]})


@app.route("/api/modbus-profiles", methods=["GET"])
def get_modbus_profiles():
    return jsonify(load_data().get("modbusProfiles", []))


@app.route("/api/modbus-profiles", methods=["PUT"])
def replace_modbus_profiles():
    data = load_data()
    payload = request.get_json(silent=True) or {}
    profiles = payload if isinstance(payload, list) else payload.get("profiles", [])
    data["modbusProfiles"] = [normalize_modbus_profile(item) for item in profiles if isinstance(item, dict)] or [default_modbus_profile()]
    push_operation_history(data, "modbus_profiles.replace", {"count": len(data['modbusProfiles'])})
    add_log("success", "Modbus 模板已更新")
    save_data(data)
    return jsonify({"success": True, "data": data["modbusProfiles"]})


@app.route("/api/modbus/read-registers", methods=["POST"])
def read_registers():
    data = load_data()
    payload = request.get_json(silent=True) or {}
    address = max(1, min(254, coerce_int(payload.get("address"), 1)))
    start_register = max(0, coerce_int(payload.get("startRegister"), 0))
    quantity = max(1, min(32, coerce_int(payload.get("quantity"), 1)))
    if not server_modbus_enabled():
        device_id = get_active_edge_device_id(data, payload.get("deviceId"))
        if not device_id:
            return server_modbus_disabled_response()
        result, err = execute_via_edge(device_id, "read_registers", {
            "address": address, "startRegister": start_register, "quantity": quantity,
        })
        if err:
            add_log("error", "[边缘] 寄存器读取失败：地址 %s 起始 %s 数量 %s" % (address, start_register, quantity), err)
            return jsonify({"success": False, "message": err}), 400
        add_log("success", "[边缘] 寄存器读取成功：地址 %s 起始 %s 数量 %s via %s" % (address, start_register, quantity, device_id))
        return jsonify({"success": True, "data": result, "via": device_id})
    serial_profile = get_default_serial_profile(data)
    modbus_profile = get_default_modbus_profile(data)
    try:
        values = get_transport(data).read_holding_registers(address, start_register, quantity, serial_profile, modbus_profile)
    except Exception as exc:
        add_log("error", "寄存器读取失败：地址 %s 寄存器 %s 数量 %s" % (address, start_register, quantity), str(exc))
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "data": {"address": address, "startRegister": start_register, "quantity": quantity, "values": values}})


@app.route("/api/modbus/write-register", methods=["POST"])
def write_register():
    data = load_data()
    payload = request.get_json(silent=True) or {}
    address = max(1, min(255, coerce_int(payload.get("address"), 1)))
    register = max(0, coerce_int(payload.get("register"), 0))
    value = max(0, min(65535, coerce_int(payload.get("value"), 0)))
    if not server_modbus_enabled():
        device_id = get_active_edge_device_id(data, payload.get("deviceId"))
        if not device_id:
            return server_modbus_disabled_response()
        result, err = execute_via_edge(device_id, "write_register", {
            "address": address, "register": register, "value": value,
        })
        if err:
            add_log("error", "[边缘] 寄存器写入失败：地址 %s 寄存器 %s = %s" % (address, register, value), err)
            return jsonify({"success": False, "message": err}), 400
        add_log("success", "[边缘] 寄存器写入成功：地址 %s 寄存器 %s = %s via %s" % (address, register, value, device_id))
        push_operation_history(data, "modbus.write_register.via_edge", result)
        save_data(data)
        return jsonify({"success": True, "data": result, "via": device_id})
    serial_profile = get_default_serial_profile(data)
    modbus_profile = get_default_modbus_profile(data)
    try:
        result = get_transport(data).write_single_register(address, register, value, serial_profile, modbus_profile)
    except Exception as exc:
        add_log("error", "寄存器写入失败：地址 %s 寄存器 %s = %s" % (address, register, value), str(exc))
        return jsonify({"success": False, "message": str(exc)}), 400
    push_operation_history(data, "modbus.write_register", result)
    add_log("success", "寄存器写入成功：地址 %s 寄存器 %s = %s" % (address, register, value))
    save_data(data)
    return jsonify({"success": True, "data": result})


@app.route("/api/modbus/discover", methods=["POST"])
def discover_devices():
    data = load_data()
    payload = request.get_json(silent=True) or {}
    start_address = coerce_int(payload.get("startAddress"), 1)
    end_address = coerce_int(payload.get("endAddress"), start_address)
    if not server_modbus_enabled():
        device_id = get_active_edge_device_id(data, payload.get("deviceId"))
        if not device_id:
            return server_modbus_disabled_response()
        scan_range = max(1, (end_address or start_address) - (start_address or 1) + 1)
        edge_timeout = min(10 + scan_range * 2, 120)
        result, err = execute_via_edge(device_id, "discover", {
            "address": start_address, "targetAddress": end_address,
        }, timeout=edge_timeout)
        if err:
            add_log("error", "[边缘] 地址扫描失败：%s-%s" % (start_address, end_address), err)
            return jsonify({"success": False, "message": err}), 400
        add_log("success", "[边缘] 地址扫描完成：%s-%s via %s" % (start_address, end_address, device_id))
        return jsonify({"success": True, "data": result, "via": device_id})
    found = discover_radars(data, start_address, end_address)
    return jsonify({"success": True, "data": {"found": found}})


@app.route("/api/modbus/address/program", methods=["POST"])
def program_address():
    data = load_data()
    payload = request.get_json(silent=True) or {}
    source_address = coerce_int(payload.get("sourceAddress"), None)
    target_address = coerce_int(payload.get("targetAddress"), None)
    slot_id = str(payload.get("slotId") or "").strip() or None
    name = str(payload.get("name") or "").strip() or None
    if source_address is None or target_address is None:
        return jsonify({"success": False, "message": "sourceAddress 和 targetAddress 必填"}), 400
    if not server_modbus_enabled():
        device_id = get_active_edge_device_id(data, payload.get("deviceId"))
        if not device_id:
            return server_modbus_disabled_response()
        result, err = execute_via_edge(device_id, "change_address", {
            "address": source_address, "targetAddress": target_address,
        })
        if err:
            add_log("error", "[边缘] 地址编程失败：%s → %s" % (source_address, target_address), err)
            return jsonify({"success": False, "message": err}), 400
        # 地址编程成功后在平台侧更新雷达记录
        try:
            radar, slot = program_radar_address(data, source_address, target_address, slot_id=slot_id, radar_name=name)
            add_log("success", "[边缘] 地址编程成功：%s → %s via %s" % (source_address, target_address, device_id))
            return jsonify({"success": True, "data": {"radar": radar, "slot": slot, "edgeResult": result, "via": device_id}})
        except (ValueError, KeyError, RuntimeError) as exc:
            add_log("warning", "[边缘] 地址编程成功但平台记录更新失败：%s" % str(exc))
            return jsonify({"success": True, "data": {"edgeResult": result, "via": device_id, "warning": str(exc)}})
    try:
        radar, slot = program_radar_address(data, source_address, target_address, slot_id=slot_id, radar_name=name)
    except (ValueError, KeyError, RuntimeError) as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "data": {"radar": radar, "slot": slot}})


@app.route("/api/modbus/poll-all", methods=["POST"])
def poll_all_endpoint():
    data = load_data()
    payload = request.get_json(silent=True) or {}
    if not server_modbus_enabled():
        device_id = get_active_edge_device_id(data, payload.get("deviceId"))
        if not device_id:
            return server_modbus_disabled_response()
        # 找到所有已绑定槽位的雷达，逐一下发 read_distance 命令
        bound_radars = [
            r for r in data.get("radars", [])
            if r.get("address") and any(
                s.get("radarId") == r.get("id")
                for s in data.get("layout", {}).get("slots", [])
            )
        ]
        if not bound_radars:
            return jsonify({"success": True, "data": {"results": [], "via": device_id, "message": "无已绑定雷达"}})
        edge_timeout = max(22, len(bound_radars) * 7)
        results = []
        for radar in bound_radars:
            result, err = execute_via_edge(device_id, "read_distance", {
                "address": radar["address"],
            }, timeout=edge_timeout)
            results.append({"radarId": radar["id"], "address": radar["address"],
                            "success": err is None, "data": result, "message": err or ""})
        add_log("info", "[边缘] 全量轮询完成：%d 台雷达 via %s" % (len(bound_radars), device_id))
        return jsonify({"success": True, "data": {"results": results, "via": device_id}})
    with POLL_LOCK:
        results = poll_all_bound_radars(data)
    add_log("info", "已执行一次全量轮询")
    return jsonify({"success": True, "data": {"results": results}})


@app.route("/api/modbus/poll-one/<radar_id>", methods=["POST"])
def poll_one_endpoint(radar_id):
    data = load_data()
    payload = request.get_json(silent=True) or {}
    radar = get_radar(data, radar_id)
    if radar is None:
        return jsonify({"success": False, "message": "radar not found"}), 404
    if not server_modbus_enabled():
        device_id = get_active_edge_device_id(data, payload.get("deviceId"))
        if not device_id:
            return server_modbus_disabled_response()
        address = radar.get("address")
        if not address:
            return jsonify({"success": False, "message": "雷达未分配 Modbus 地址"}), 400
        result, err = execute_via_edge(device_id, "read_distance", {"address": address})
        if err:
            radar["online"] = False
            radar["lastPollTime"] = now_str()
            radar["lastError"] = err
            save_data(data)
            add_log("error", "[边缘] 单雷达轮询失败：地址 %s" % address, err)
            return jsonify({"success": False, "message": err}), 400
        # 将边缘返回的距离更新到雷达记录
        distance_mm = result.get("distanceMm") or result.get("distance_mm")
        if distance_mm is not None:
            radar["lastDistanceMm"] = coerce_int(distance_mm, radar.get("lastDistanceMm"))
        radar["online"] = True
        radar["lastPollTime"] = now_str()
        radar["lastError"] = ""
        save_data(data)
        add_log("success", "[边缘] 单雷达轮询成功：地址 %s 距离 %s mm via %s" % (address, distance_mm, device_id))
        return jsonify({"success": True, "data": dict(result, radarId=radar_id, address=address, via=device_id)})
    try:
        result = poll_radar(data, radar)
        save_data(data)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        radar["online"] = False
        radar["lastPollTime"] = now_str()
        radar["lastError"] = str(exc)
        save_data(data)
        return jsonify({"success": False, "message": str(exc)}), 400


@app.route("/api/logs", methods=["GET"])
def get_logs():
    return jsonify(load_logs())


@app.route("/api/logs", methods=["DELETE"])
def clear_logs():
    save_logs([])
    add_log("info", "平台日志已清空")
    return jsonify({"success": True})


if __name__ == "__main__":  # pragma: no cover
    if not os.path.exists(DATA_FILE):
        reset_data_store()
    add_log("info", "雷达地址绑定平台后端启动")
    start_background_poller()
    app.run(host="127.0.0.1", port=5001, debug=False)
