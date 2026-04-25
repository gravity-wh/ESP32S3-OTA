#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ESP32 OTA 固件管理后端服务

能力：
- 统一发布接口：上传 bin 时同时生成 version.txt / manifest.json
- 兼容旧接口：保留 /upload 与 /api/release-metadata
- 设备心跳：记录在线状态、当前版本、升级时间与 OTA 结果
- 固件清理：支持删除旧固件，避免版本目录越来越乱
"""

import datetime
import hashlib
import json
import logging
import os

from flask import Flask, has_request_context, jsonify, request
from werkzeug.utils import secure_filename

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.environ.get("OTA_UPLOAD_FOLDER", os.path.join(BASE_DIR, "firmware"))
LOG_DIR = os.environ.get("OTA_LOG_DIR", "/var/log/ota")
LOG_FILE = os.environ.get("OTA_LOG_FILE", os.path.join(LOG_DIR, "ota_logs.json"))
DATA_FILE = os.environ.get("OTA_DATA_FILE", os.path.join(LOG_DIR, "ota_data.json"))
SERVER_LOG_FILE = os.environ.get("OTA_SERVER_LOG_FILE", os.path.join(LOG_DIR, "ota_server.log"))
PUBLIC_BASE_URL = os.environ.get("OTA_PUBLIC_BASE_URL", "http://116.62.218.129:8080").rstrip("/")
MAX_FILE_SIZE = 20 * 1024 * 1024
ALLOWED_FIRMWARE_EXTENSIONS = {".bin"}
DEVICE_ONLINE_TIMEOUT_SECONDS = int(os.environ.get("OTA_DEVICE_ONLINE_TIMEOUT_SECONDS", "120"))

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(SERVER_LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


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


def default_data():
    return {
        "firmwares": [],
        "devices": [],
        "releaseMetadata": {},
    }


def ensure_data_shape(data):
    if not isinstance(data, dict):
        data = {}
    data.setdefault("firmwares", [])
    data.setdefault("devices", [])
    data.setdefault("releaseMetadata", {})

    if not isinstance(data["firmwares"], list):
        data["firmwares"] = []
    if not isinstance(data["devices"], list):
        data["devices"] = []
    if not isinstance(data["releaseMetadata"], dict):
        data["releaseMetadata"] = {}

    return data


def load_logs():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.error("加载日志失败：%s", exc)
    return []


def save_logs(logs):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error("保存日志失败：%s", exc)


def add_log(log_type, message):
    logs = load_logs()
    log_entry = {
        "type": log_type,
        "message": message,
        "time": now_str(),
    }
    logs.append(log_entry)
    save_logs(logs)
    logger.info("[%s] %s", log_type, message)
    return log_entry


def load_data():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return normalize_data(json.load(f))
    except Exception as exc:
        logger.error("加载数据失败：%s", exc)
    return default_data()


def save_data(data):
    normalized = normalize_data(data)
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error("保存数据失败：%s", exc)


def read_text_file(file_storage):
    content = file_storage.read()
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8-sig")
        except Exception:
            return content.decode("utf-8", errors="ignore")
    return str(content)


def split_notes(value):
    if not value:
        return []
    return [line.strip() for line in value.replace("\r", "").split("\n") if line.strip()]


def parse_version_text(version_text):
    for line in version_text.splitlines():
        value = line.strip()
        if value:
            return value
    return version_text.strip()


def get_release_file_path(filename):
    return os.path.join(app.config["UPLOAD_FOLDER"], filename)


def public_base_url():
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    if has_request_context():
        return request.host_url.rstrip("/")
    return "http://127.0.0.1:8080"


def firmware_url(filename):
    return "%s/firmware/%s" % (public_base_url(), filename)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def firmware_record_sort_key(record):
    timestamp = parse_time(record.get("uploadTime")) or datetime.datetime.min
    return (1 if record.get("isCurrent") else 0, timestamp.strftime("%Y%m%d%H%M%S"))


def normalize_mac(mac):
    return (mac or "").strip().upper()


def normalize_tags(tags):
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = [item.strip() for item in tags.replace("，", ",").split(",")]
    if not isinstance(tags, list):
        return []

    result = []
    for tag in tags:
        value = str(tag).strip()
        if value and value not in result:
            result.append(value)
    return result


def normalize_firmware_record(record):
    manifest = record.get("manifest") if isinstance(record.get("manifest"), dict) else {}
    notes = record.get("notes") if isinstance(record.get("notes"), list) else []
    if not notes and manifest.get("notes"):
        notes = split_notes("\n".join([str(item) for item in manifest.get("notes", [])]))

    description = str(record.get("description") or "").strip()
    if not description and manifest.get("description"):
        description = str(manifest.get("description"))

    return {
        "name": str(record.get("name") or "").strip(),
        "version": str(record.get("version") or "").strip(),
        "size": int(record.get("size") or 0),
        "uploadTime": str(record.get("uploadTime") or "").strip(),
        "versionText": str(record.get("versionText") or "").strip(),
        "description": description,
        "notes": notes,
        "sha256": str(record.get("sha256") or "").strip(),
        "isCurrent": bool(record.get("isCurrent")),
        "manifest": manifest,
        "publishedAt": str(record.get("publishedAt") or "").strip(),
    }


def normalize_device_record(record):
    normalized = {
        "id": str(record.get("id") or "").strip(),
        "name": str(record.get("name") or "未命名设备").strip(),
        "label": str(record.get("label") or "").strip(),
        "firmware": str(record.get("firmware") or "").strip(),
        "firmwareFile": str(record.get("firmwareFile") or "").strip(),
        "appVersion": str(record.get("appVersion") or "").strip(),
        "status": str(record.get("status") or "offline").strip(),
        "registerTime": str(record.get("registerTime") or "").strip(),
        "lastUpdate": str(record.get("lastUpdate") or "").strip(),
        "lastSeen": str(record.get("lastSeen") or "").strip(),
        "lastFirmwareChangeAt": str(record.get("lastFirmwareChangeAt") or "").strip(),
        "lastUpgradeAt": str(record.get("lastUpgradeAt") or "").strip(),
        "lastUpgradeFrom": str(record.get("lastUpgradeFrom") or "").strip(),
        "lastUpgradeTo": str(record.get("lastUpgradeTo") or "").strip(),
        "lastOtaCheckAt": str(record.get("lastOtaCheckAt") or "").strip(),
        "lastOtaState": str(record.get("lastOtaState") or "").strip(),
        "lastOtaResult": str(record.get("lastOtaResult") or "").strip(),
        "lastOtaMessage": str(record.get("lastOtaMessage") or "").strip(),
        "ip": str(record.get("ip") or "").strip(),
        "mac": normalize_mac(record.get("mac")),
        "networkMode": str(record.get("networkMode") or "").strip(),
        "uplink": str(record.get("uplink") or "").strip(),
        "modeSelectGpio": record.get("modeSelectGpio"),
        "tags": normalize_tags(record.get("tags")),
        "notes": str(record.get("notes") or "").strip(),
        "telemetry": record.get("telemetry") if isinstance(record.get("telemetry"), dict) else {},
    }

    seen_time = parse_time(normalized["lastSeen"]) or parse_time(normalized["lastUpdate"])
    if seen_time:
        delta = (now() - seen_time).total_seconds()
        normalized["online"] = delta <= DEVICE_ONLINE_TIMEOUT_SECONDS
        if not normalized["status"] or normalized["status"] == "offline":
            normalized["status"] = "online" if normalized["online"] else "offline"
    else:
        normalized["online"] = False
        normalized["status"] = "offline"

    return normalized


def find_firmware(data, filename):
    for firmware in data["firmwares"]:
        if firmware.get("name") == filename:
            return firmware
    return None


def normalize_data(data):
    payload = ensure_data_shape(data)
    release_metadata = payload.get("releaseMetadata", {})
    if not isinstance(release_metadata, dict):
        release_metadata = {}
        payload["releaseMetadata"] = release_metadata

    current_file = str(
        release_metadata.get("file")
        or release_metadata.get("firmwareName")
        or release_metadata.get("manifest", {}).get("file")
        or ""
    ).strip()

    deduped = {}
    for entry in payload["firmwares"]:
        record = normalize_firmware_record(entry if isinstance(entry, dict) else {})
        if not record["name"]:
            continue
        previous = deduped.get(record["name"])
        if previous is None:
            deduped[record["name"]] = record
            continue
        prev_time = parse_time(previous.get("uploadTime")) or datetime.datetime.min
        new_time = parse_time(record.get("uploadTime")) or datetime.datetime.min
        if new_time >= prev_time:
            merged = previous.copy()
            for key, value in record.items():
                if value not in ("", [], {}, 0, False):
                    merged[key] = value
            deduped[record["name"]] = merged

    existing_files = set()
    for name in os.listdir(UPLOAD_FOLDER):
        if name.lower().endswith(".bin"):
            existing_files.add(name)

    for filename in existing_files:
        if filename not in deduped:
            path = get_release_file_path(filename)
            deduped[filename] = {
                "name": filename,
                "version": "",
                "size": os.path.getsize(path),
                "uploadTime": now_str(),
                "versionText": "",
                "description": "",
                "notes": [],
                "sha256": "",
                "isCurrent": False,
                "manifest": {},
                "publishedAt": "",
            }

    firmware_items = []
    for filename, record in deduped.items():
        path = get_release_file_path(filename)
        if not os.path.exists(path):
            continue
        record["size"] = os.path.getsize(path)
        record["isCurrent"] = filename == current_file
        firmware_items.append(record)

    firmware_items.sort(key=firmware_record_sort_key, reverse=True)
    payload["firmwares"] = firmware_items

    devices = []
    for item in payload["devices"]:
        devices.append(normalize_device_record(item if isinstance(item, dict) else {}))
    devices.sort(key=lambda item: ((1 if item.get("online") else 0), item.get("lastSeen", "")), reverse=True)
    payload["devices"] = devices

    return payload


def upsert_firmware_record(
    data,
    filename,
    version=None,
    version_text=None,
    description=None,
    notes=None,
    sha256_value=None,
    manifest=None,
    is_current=None,
    upload_time=None,
    published_at=None,
):
    firmware = find_firmware(data, filename)
    if firmware is None:
        firmware = normalize_firmware_record({"name": filename})
        data["firmwares"].append(firmware)

    path = get_release_file_path(filename)
    if os.path.exists(path):
        firmware["size"] = os.path.getsize(path)

    if upload_time is not None:
        firmware["uploadTime"] = upload_time
    elif not firmware.get("uploadTime"):
        firmware["uploadTime"] = now_str()

    if version is not None:
        firmware["version"] = version
    if version_text is not None:
        firmware["versionText"] = version_text
    if description is not None:
        firmware["description"] = description
    if notes is not None:
        firmware["notes"] = notes
    if sha256_value is not None:
        firmware["sha256"] = sha256_value
    if manifest is not None:
        firmware["manifest"] = manifest
    if published_at is not None:
        firmware["publishedAt"] = published_at
    if is_current is not None:
        firmware["isCurrent"] = is_current

    return firmware


def mark_current_release(data, filename):
    for firmware in data["firmwares"]:
        firmware["isCurrent"] = firmware.get("name") == filename


def build_manifest(version, filename, size, sha256_value, description, notes):
    manifest = {
        "version": version,
        "file": filename,
        "size": size,
        "sha256": sha256_value,
        "url": firmware_url(filename),
        "notes": notes,
    }
    if description:
        manifest["description"] = description
    return manifest


def write_release_metadata(version, manifest, filename, description, notes):
    with open(get_release_file_path("version.txt"), "w", encoding="utf-8") as f:
        f.write(version.strip() + "\n")
    with open(get_release_file_path("manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return {
        "version": version,
        "versionText": version,
        "versionFile": "version.txt",
        "manifest": manifest,
        "manifestFile": "manifest.json",
        "file": filename,
        "firmwareName": filename,
        "description": description,
        "notes": notes,
        "updatedAt": now_str(),
    }


def publish_release(filename, version, description, notes):
    path = get_release_file_path(filename)
    size = os.path.getsize(path)
    sha256_value = sha256_file(path)
    published_at = now_str()
    manifest = build_manifest(version, filename, size, sha256_value, description, notes)

    data = load_data()
    metadata = write_release_metadata(version, manifest, filename, description, notes)
    data["releaseMetadata"] = metadata
    firmware = upsert_firmware_record(
        data,
        filename,
        version=version,
        version_text=version,
        description=description,
        notes=notes,
        sha256_value=sha256_value,
        manifest=manifest,
        is_current=True,
        upload_time=published_at,
        published_at=published_at,
    )
    mark_current_release(data, filename)
    save_data(data)
    return {
        "release": metadata,
        "firmware": firmware,
    }


def find_device(data, device_id=None, mac=None):
    device_id = (device_id or "").strip()
    mac = normalize_mac(mac)
    for device in data["devices"]:
        if device_id and device.get("id") == device_id:
            return device
        if mac and normalize_mac(device.get("mac")) == mac:
            return device
    return None


def build_device_payload(req_data, existing=None):
    existing = existing or {}
    current_time = now_str()
    device_id = str(
        req_data.get("id")
        or req_data.get("deviceId")
        or existing.get("id")
        or req_data.get("mac")
        or req_data.get("ip")
        or ""
    ).strip()
    ip = str(req_data.get("ip") or existing.get("ip") or request.remote_addr or "").strip()
    mac = normalize_mac(req_data.get("mac") or existing.get("mac"))
    name = str(req_data.get("name") or existing.get("name") or "ESP Device").strip()
    label = str(req_data.get("label") or existing.get("label") or "").strip()
    tags = normalize_tags(req_data.get("tags", existing.get("tags", [])))
    notes = str(req_data.get("notes") or existing.get("notes") or "").strip()
    status = str(req_data.get("status") or existing.get("status") or "online").strip()
    firmware_name = str(req_data.get("firmware") or req_data.get("firmwareFile") or existing.get("firmware") or "").strip()
    app_version = str(req_data.get("appVersion") or req_data.get("version") or existing.get("appVersion") or "").strip()
    network_mode = str(req_data.get("networkMode") or existing.get("networkMode") or "").strip()
    uplink = str(req_data.get("uplink") or existing.get("uplink") or "").strip()
    mode_select_gpio = req_data.get("modeSelectGpio", existing.get("modeSelectGpio"))
    register_time = str(existing.get("registerTime") or current_time)

    telemetry = existing.get("telemetry", {}).copy() if isinstance(existing.get("telemetry"), dict) else {}
    incoming_telemetry = req_data.get("telemetry")
    if isinstance(incoming_telemetry, dict):
        telemetry.update(incoming_telemetry)
    if network_mode:
        telemetry["networkMode"] = network_mode
    if uplink:
        telemetry["uplink"] = uplink
    if mode_select_gpio is not None:
        telemetry["modeSelectGpio"] = mode_select_gpio
    if "distanceMm" in req_data:
        telemetry["distanceMm"] = req_data.get("distanceMm")
    if "pollOkCount" in req_data:
        telemetry["pollOkCount"] = req_data.get("pollOkCount")
    if "pollErrorCount" in req_data:
        telemetry["pollErrorCount"] = req_data.get("pollErrorCount")

    device = normalize_device_record({
        "id": device_id,
        "name": name,
        "label": label,
        "firmware": firmware_name,
        "firmwareFile": firmware_name,
        "appVersion": app_version,
        "status": status,
        "registerTime": register_time,
        "lastUpdate": current_time,
        "lastSeen": current_time,
        "ip": ip,
        "mac": mac,
        "networkMode": network_mode,
        "uplink": uplink,
        "modeSelectGpio": mode_select_gpio,
        "tags": tags,
        "notes": notes,
        "telemetry": telemetry,
        "lastFirmwareChangeAt": existing.get("lastFirmwareChangeAt", ""),
        "lastUpgradeAt": existing.get("lastUpgradeAt", ""),
        "lastUpgradeFrom": existing.get("lastUpgradeFrom", ""),
        "lastUpgradeTo": existing.get("lastUpgradeTo", ""),
        "lastOtaCheckAt": existing.get("lastOtaCheckAt", ""),
        "lastOtaState": existing.get("lastOtaState", ""),
        "lastOtaResult": existing.get("lastOtaResult", ""),
        "lastOtaMessage": existing.get("lastOtaMessage", ""),
    })

    previous_version = str(existing.get("appVersion") or "").strip()
    if app_version and previous_version and previous_version != app_version:
        device["lastFirmwareChangeAt"] = current_time
        device["lastUpgradeAt"] = current_time
        device["lastUpgradeFrom"] = previous_version
        device["lastUpgradeTo"] = app_version
    elif app_version and not previous_version and not device.get("lastFirmwareChangeAt"):
        device["lastFirmwareChangeAt"] = current_time

    ota_payload = req_data.get("ota")
    if isinstance(ota_payload, dict):
        ota_state = str(ota_payload.get("state") or "")
        ota_result = str(ota_payload.get("result") or "")
        if ota_payload.get("state"):
            device["lastOtaState"] = ota_state
        if ota_payload.get("result"):
            device["lastOtaResult"] = ota_result
        if ota_payload.get("message"):
            device["lastOtaMessage"] = str(ota_payload.get("message"))
        if ota_payload.get("checkAt"):
            device["lastOtaCheckAt"] = str(ota_payload.get("checkAt"))
        else:
            device["lastOtaCheckAt"] = current_time
        if ota_state in ("REBOOT", "FAIL") or ota_result in ("success", "failed"):
            if ota_payload.get("upgradeAt"):
                device["lastUpgradeAt"] = str(ota_payload.get("upgradeAt"))
            elif not device.get("lastUpgradeAt"):
                device["lastUpgradeAt"] = current_time
            if ota_payload.get("fromVersion"):
                device["lastUpgradeFrom"] = str(ota_payload.get("fromVersion"))
            if ota_payload.get("toVersion"):
                device["lastUpgradeTo"] = str(ota_payload.get("toVersion"))

    return device


@app.route("/upload", methods=["POST"])
@app.route("/api/upload", methods=["POST"])
def upload_file():
    try:
        if "file" not in request.files:
            add_log("error", "上传失败：未找到文件")
            return jsonify({"success": False, "message": "未找到文件"}), 400

        file = request.files["file"]
        if not file or file.filename == "":
            add_log("error", "上传失败：文件名为空")
            return jsonify({"success": False, "message": "文件名为空"}), 400

        filename = secure_filename(file.filename)
        if os.path.splitext(filename)[1].lower() not in ALLOWED_FIRMWARE_EXTENSIONS:
            return jsonify({"success": False, "message": "只支持 .bin 文件"}), 400

        filepath = get_release_file_path(filename)
        file.save(filepath)
        upload_time = now_str()
        data = load_data()
        firmware = upsert_firmware_record(data, filename, upload_time=upload_time)
        save_data(data)

        add_log("success", "固件上传成功：%s (%.2f KB)" % (filename, firmware["size"] / 1024.0))
        return jsonify({"success": True, "message": "上传成功", "data": firmware})
    except Exception as exc:
        add_log("error", "上传失败：%s" % exc)
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/releases", methods=["POST"])
def upload_release():
    try:
        if "file" not in request.files:
            return jsonify({"success": False, "message": "请上传 .bin 固件文件"}), 400

        file = request.files["file"]
        version = (request.form.get("version") or "").strip()
        description = (request.form.get("description") or "").strip()
        notes = split_notes(request.form.get("notes") or "")

        if not file or file.filename == "":
            return jsonify({"success": False, "message": "文件名为空"}), 400
        if not version:
            return jsonify({"success": False, "message": "版本号不能为空"}), 400

        filename = secure_filename(file.filename)
        if os.path.splitext(filename)[1].lower() not in ALLOWED_FIRMWARE_EXTENSIONS:
            return jsonify({"success": False, "message": "只支持 .bin 文件"}), 400

        filepath = get_release_file_path(filename)
        file.save(filepath)
        payload = publish_release(filename, version, description, notes)
        add_log("success", "发布新固件：%s -> %s" % (version, filename))
        return jsonify({"success": True, "message": "发布成功", "data": payload})
    except Exception as exc:
        add_log("error", "发布失败：%s" % exc)
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/release-metadata", methods=["GET"])
def get_release_metadata():
    data = load_data()
    return jsonify(data.get("releaseMetadata", {}))


@app.route("/api/release-metadata", methods=["POST"])
def upload_release_metadata():
    try:
        version_file = request.files.get("version_file")
        manifest_file = request.files.get("manifest_file")
        version_text_input = (request.form.get("version_text") or "").strip()
        firmware_name = (request.form.get("firmware_name") or "").strip()

        if not version_file and not manifest_file and not version_text_input:
            return jsonify({"success": False, "message": "请上传 version.txt 或 manifest.json"}), 400

        version_text = None
        if version_file:
            version_text = read_text_file(version_file).strip()
        elif version_text_input:
            version_text = version_text_input

        manifest_data = None
        if manifest_file:
            manifest_text = read_text_file(manifest_file).strip()
            manifest_data = json.loads(manifest_text)
            if not isinstance(manifest_data, dict):
                return jsonify({"success": False, "message": "manifest.json 顶层必须是对象"}), 400

        data = load_data()
        metadata = data.get("releaseMetadata", {}).copy()
        if version_text:
            metadata["version"] = parse_version_text(version_text)
            metadata["versionText"] = version_text
            metadata["versionFile"] = "version.txt"
            with open(get_release_file_path("version.txt"), "w", encoding="utf-8") as f:
                f.write(version_text + "\n")
        if manifest_data:
            metadata["manifest"] = manifest_data
            metadata["manifestFile"] = "manifest.json"
            metadata["file"] = manifest_data.get("file", firmware_name)
            metadata["firmwareName"] = manifest_data.get("file", firmware_name)
            metadata["description"] = str(manifest_data.get("description") or "")
            metadata["notes"] = manifest_data.get("notes", [])
            with open(get_release_file_path("manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, ensure_ascii=False, indent=2)
            if manifest_data.get("version"):
                metadata["version"] = str(manifest_data["version"])
        metadata["updatedAt"] = now_str()
        data["releaseMetadata"] = metadata

        if firmware_name:
            upsert_firmware_record(
                data,
                firmware_name,
                version=metadata.get("version"),
                version_text=metadata.get("versionText"),
                description=metadata.get("description", ""),
                notes=metadata.get("notes", []),
                manifest=metadata.get("manifest", {}),
                sha256_value=metadata.get("manifest", {}).get("sha256"),
                is_current=(firmware_name == metadata.get("file")),
            )
            if metadata.get("file"):
                mark_current_release(data, metadata.get("file"))

        save_data(data)
        add_log("success", "版本元数据已更新：%s" % metadata.get("version", "unknown"))
        return jsonify({"success": True, "message": "版本元数据上传成功", "data": metadata})
    except Exception as exc:
        add_log("error", "版本元数据上传失败：%s" % exc)
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/firmwares", methods=["GET"])
def get_firmwares():
    data = load_data()
    return jsonify(data["firmwares"])


@app.route("/api/firmwares/<path:filename>", methods=["DELETE"])
def delete_firmware(filename):
    try:
        safe_name = secure_filename(filename)
        data = load_data()
        release_file = str(data.get("releaseMetadata", {}).get("file") or "").strip()
        if safe_name == release_file:
            return jsonify({"success": False, "message": "当前发布中的固件不能直接删除"}), 400

        path = get_release_file_path(safe_name)
        existed = os.path.exists(path)
        if existed:
            os.remove(path)

        data["firmwares"] = [fw for fw in data["firmwares"] if fw.get("name") != safe_name]
        save_data(data)
        add_log("success", "删除旧固件：%s" % safe_name)
        return jsonify({"success": True, "message": "固件已删除", "deleted": safe_name, "fileExisted": existed})
    except Exception as exc:
        add_log("error", "删除固件失败：%s" % exc)
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/devices", methods=["GET"])
def get_devices():
    data = load_data()
    return jsonify(data["devices"])


@app.route("/api/devices", methods=["POST"])
def add_or_update_device():
    try:
        req_data = request.get_json(silent=True) or {}
        data = load_data()
        existing = find_device(data, device_id=req_data.get("id"), mac=req_data.get("mac"))
        device = build_device_payload(req_data, existing=existing)
        if not device.get("id"):
            return jsonify({"success": False, "message": "设备 ID / MAC / IP 不能都为空"}), 400

        if existing is None:
            data["devices"].append(device)
            action = "添加"
        else:
            existing.clear()
            existing.update(device)
            action = "更新"

        save_data(data)
        add_log("success", "%s设备：%s (%s)" % (action, device["name"], device["id"]))
        return jsonify({"success": True, "data": device})
    except Exception as exc:
        add_log("error", "设备保存失败：%s" % exc)
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/devices/<device_id>", methods=["PUT"])
def update_device(device_id):
    req_data = request.get_json(silent=True) or {}
    req_data["id"] = device_id
    return add_or_update_device()


@app.route("/api/devices/heartbeat", methods=["POST"])
def device_heartbeat():
    try:
        req_data = request.get_json(silent=True) or {}
        if not req_data and request.data:
            try:
                req_data = json.loads(request.data.decode("utf-8", errors="ignore"))
            except Exception:
                req_data = {}
        if not isinstance(req_data, dict):
            return jsonify({"success": False, "message": "请求体必须是 JSON 对象"}), 400

        data = load_data()
        existing = find_device(data, device_id=req_data.get("id") or req_data.get("deviceId"), mac=req_data.get("mac"))
        device = build_device_payload(req_data, existing=existing)
        if not device.get("id"):
            return jsonify({"success": False, "message": "设备 ID / MAC / IP 不能都为空"}), 400

        if existing is None:
            data["devices"].append(device)
            action = "首次心跳"
        else:
            existing.clear()
            existing.update(device)
            action = "心跳更新"

        save_data(data)
        add_log("info", "%s：%s %s %s" % (
            action,
            device.get("name", "ESP Device"),
            device.get("appVersion", "-"),
            device.get("ip", "-"),
        ))
        return jsonify({
            "success": True,
            "message": "heartbeat received",
            "data": device,
            "release": data.get("releaseMetadata", {}),
        })
    except Exception as exc:
        add_log("error", "设备心跳失败：%s" % exc)
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/logs", methods=["GET"])
def get_logs():
    logs = load_logs()
    filter_type = request.args.get("type", "all")
    if filter_type != "all":
        logs = [item for item in logs if item.get("type") == filter_type]
    return jsonify(logs[-100:])


@app.route("/api/logs", methods=["DELETE"])
def clear_logs():
    save_logs([])
    add_log("info", "日志已清空")
    return jsonify({"success": True})


@app.route("/health", methods=["GET"])
def health_check():
    data = load_data()
    online_count = len([item for item in data["devices"] if item.get("online")])
    return jsonify({
        "status": "ok",
        "message": "OTA Server is running",
        "currentRelease": data.get("releaseMetadata", {}).get("version", ""),
        "onlineDevices": online_count,
    })


if __name__ == "__main__":
    logger.info("OTA 服务器启动...")
    add_log("info", "OTA 后端服务启动")
    app.run(host="127.0.0.1", port=5000, debug=False)
