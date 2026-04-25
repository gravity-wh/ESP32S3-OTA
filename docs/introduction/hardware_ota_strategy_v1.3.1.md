# ESP32-S3 Radar OLED v1.3.1 硬件方案与 OTA 策略

本文记录 `v1.3.1` 的最终双链路策略：Wi-Fi 固定用于设备心跳、雷达数据上报和平台显示，4G AT 模块固定用于 OTA manifest 查询、固件包下载和 OTA 写入。`GPIO6` 通信模式选择功能已取消。

## 1. 硬件连接

### 1.1 Wi-Fi 链路

ESP32-S3 使用板载 Wi-Fi 连接业务网络。Wi-Fi 不再承担 OTA 固件包下载，主要负责：

- 设备在线心跳。
- 雷达距离、轮询成功计数、错误计数等 telemetry 上报。
- OTA 状态上报，例如 `4GCHK`、`LATEST`、`REBOOT`。
- 固件管理平台设备列表显示。

### 1.2 4G AT 模块链路

4G 模块使用 ESP32-S3 的 `UART2`，默认波特率 `115200`。

| 连接项 | ESP32-S3 | 4G 模块 |
| --- | --- | --- |
| UART TX | GPIO4 | 模块 RX |
| UART RX | GPIO5 | 模块 TX |
| GND | GND | GND |
| 电源 | 独立稳定供电 | 按模块规格供电 |

注意事项：

- GPIO19/GPIO20 不再作为 AT 模块串口使用，避免与 ESP32-S3 USB 专用功能冲突。
- 4G 模块峰值电流较高，建议使用独立电源或足够裕量的电源模块。
- 串口交叉连接：ESP32 `TX` 接模块 `RX`，ESP32 `RX` 接模块 `TX`。

### 1.3 GPIO6 状态

`v1.3.1` 不再读取 GPIO6，也不再根据 GPIO6 切换 Wi-Fi/4G 模式。

- GPIO6 可留空或用于后续其他功能。
- 心跳上报中 `modeSelectGpio` 置为 `null`。
- 固件管理页面不再显示 `GPIO-` 这类无效模式选择信息。

## 2. 软件策略

### 2.1 启动策略

设备启动后固定并行启动两个链路：

1. Wi-Fi 上报服务：连接 Wi-Fi，启动设备心跳和雷达数据上报。
2. 4G OTA 服务：初始化 UART2，配置 EC801E/AT 模块联网，然后定时通过 4G 查询 OTA manifest。

启动日志关键标识：

```text
Communication policy: Wi-Fi reports data, 4G transports OTA packages
Wi-Fi data reporting is ready; OTA download is handled by 4G module
Cellular UART2 ready: TX=4 RX=5 baud=115200
4G network is ready
```

### 2.2 Wi-Fi 上报内容

Wi-Fi 心跳继续 POST 到：

```text
http://116.62.218.129:8080/api/devices/heartbeat
```

关键字段：

| 字段 | 值 |
| --- | --- |
| `networkMode` | `wifi` |
| `uplink` | `wifi` |
| `modeSelectGpio` | `null` |
| `telemetry.distanceMm` | 雷达距离 |
| `telemetry.pollOkCount` | 雷达成功读取次数 |
| `telemetry.pollErrorCount` | 雷达错误次数 |
| `ota.state` | 4G OTA 当前状态 |

### 2.3 4G OTA 流程

4G 模块只负责 OTA 相关 HTTP 传输，不负责业务数据上报。

流程：

1. UART2 初始化：`TX=GPIO4`、`RX=GPIO5`、`115200`。
2. `AT` 探测模块。
3. `ATE0` 关闭回显。
4. `ATI` 读取模块型号。
5. `AT+CPIN?` 检查 SIM。
6. `AT+CSQ` 检查信号。
7. `AT+CGATT?` 检查网络附着。
8. `AT+QICSGP` 配置 APN。
9. `AT+QIACT` 激活 PDP。
10. `AT+QHTTPGET/QHTTPREAD` 拉取 manifest。
11. 远端版本高于当前版本时，通过 4G 下载固件包并写入 OTA 分区。

## 3. 分区策略

沿用 `v1.3.0` 开始启用的 16MB 自定义分区表 `partitions_4m_ota.csv`。

| 分区 | 偏移 | 大小 | 用途 |
| --- | --- | --- | --- |
| `nvs` | `0x9000` | `0x6000` | Wi-Fi/NVS 配置 |
| `otadata` | `0xf000` | `0x2000` | OTA 启动选择数据 |
| `phy_init` | `0x11000` | `0x1000` | PHY 初始化 |
| `factory` | `0x20000` | `0x400000` | 串口全量烧录默认 app |
| `ota_0` | `0x420000` | `0x400000` | OTA app 槽 0 |
| `ota_1` | `0x820000` | `0x400000` | OTA app 槽 1 |
| `storage` | `0xc20000` | `0x3e0000` | 预留数据区 |

迁移注意：

- 已经完成 `v1.3.0` 或更高版本全量烧录的设备，可以继续使用 OTA 分区升级。
- 从旧分区表迁移到该布局时，仍必须串口全量烧录一次 bootloader、partition table、otadata 和 factory app。

## 4. v1.3.1 发布信息

当前服务器发布版本：

- 版本：`v1.3.1`
- 文件：`radar_oled_ota_v1.3.1.bin`
- 大小：`972912` 字节
- SHA256：`2b70b59b821fde81107db0b4980adb4b14486f21b83aff4fcd635151f335d40a`
- OTA 传输链路：4G AT 模块
- 数据上报链路：Wi-Fi

## 5. 已验证结果

本次验证于 `2026-04-23` 完成：

- `v1.3.1` 编译通过。
- 固件已通过 `COM5` 全量烧录到 ESP32-S3。
- 启动日志显示 app version 为 `v1.3.1`。
- Wi-Fi 获取 IP：`192.168.1.115`。
- 设备心跳通过 Wi-Fi 上报，平台记录 `networkMode=wifi`、`uplink=wifi`、`modeSelectGpio=null`。
- EC801E 通过 UART2 正常响应 AT 指令。
- 4G 网络激活成功，蜂窝 IP：`10.19.68.166`。
- 4G 成功拉取线上 manifest，状态码 `200`。
- manifest 已更新到 `v1.3.1`，设备判断 `current=v1.3.1 remote=v1.3.1`，不重复升级。
