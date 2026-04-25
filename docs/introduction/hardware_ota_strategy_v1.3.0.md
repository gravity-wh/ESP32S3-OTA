# ESP32-S3 Radar OLED v1.3.0 硬件方案与 OTA 策略

本文记录 `v1.3.0` 版本的硬件连接、通信链路选择、固件服务器联动和 OTA 分区策略，适用于当前 ESP32-S3 Radar OLED + EC801E/Air780E 类 AT 指令 4G 模块方案。

## 1. 硬件连接方案

### 1.1 ESP32-S3 主控与 OLED/雷达

保留前序 OLED 显示、雷达测距、Wi-Fi OTA 和设备心跳能力。当前固件启动后会持续读取雷达数据并在 OLED/串口中显示距离、成功计数、错误计数、版本号和 OTA 状态。

### 1.2 4G AT 模块串口连接

4G 模块使用 ESP32-S3 的 `UART2`，默认波特率 `115200`。

| 连接项 | ESP32-S3 | 4G 模块 |
| --- | --- | --- |
| UART TX | GPIO4 | 模块 RX |
| UART RX | GPIO5 | 模块 TX |
| GND | GND | GND |
| 供电 | 外部稳定供电 | 按模块规格供电 |

注意事项：

- GPIO19/GPIO20 避免作为外接 AT 模块串口使用，因为它们容易与 ESP32-S3 内部 USB 相关功能冲突。
- 4G 模块峰值电流较高，建议使用独立且裕量足够的电源，不要只依赖开发板弱 3.3V 引脚供电。
- 串口必须交叉连接：ESP32 `TX` 接模块 `RX`，ESP32 `RX` 接模块 `TX`。

### 1.3 GPIO6 通信模式选择

`GPIO6` 作为启动时通信链路选择脚。

| GPIO6 电平 | 通信模式 | 行为 |
| --- | --- | --- |
| 接地 / 低电平 | Wi-Fi 模式 | Wi-Fi 心跳上报、Wi-Fi 数据上传、Wi-Fi OTA 检查 |
| 拉高到 3.3V | 4G 模式 | 通过 UART2 AT 模块联网、4G 心跳上报、4G OTA 检查与下载 |

实现策略：

- 固件启动时读取一次 GPIO6，低电平进入 `wifi`，高电平进入 `4g`。
- 固件内部对 GPIO6 启用下拉，因此未外接时默认进入 Wi-Fi 模式。
- 如果运行中改变 GPIO6 电平，需要重启设备后才会切换模式。

## 2. 固件功能策略

### 2.1 Wi-Fi 模式

Wi-Fi 模式继承原有 OTA 与设备心跳能力。

- 设备获取局域网 IP 后访问服务器 manifest。
- 设备向 `/api/devices/heartbeat` 上报版本、IP、MAC、雷达距离和 OTA 状态。
- 心跳中新增字段：`networkMode=wifi`、`uplink=wifi`、`modeSelectGpio=6`。
- 固件管理平台的设备管理页面会显示通信链路为 Wi-Fi。

### 2.2 4G 模式

4G 模式通过 UART2 向 AT 模块发送联网与 HTTP 指令。

主要流程：

1. 初始化 UART2：`TX=GPIO4`、`RX=GPIO5`、`115200`。
2. 发送 `AT` 探测模块。
3. 检查 SIM、信号和 PS 附着状态。
4. 配置 PDP/APN，并激活网络。
5. 使用模块 HTTP AT 命令访问 OTA manifest。
6. 当服务器版本高于当前版本时，下载固件并写入 ESP-IDF OTA 分区。
7. 通过 4G POST 心跳，设备管理页面显示 `networkMode=4g`、`uplink=cellular`。

此前已验证 EC801E 模块可成功联网并通过 4G 完成 OTA 下载写入。实测模块返回包含：

- `ATI`：Quectel EC801E
- `AT+CPIN?`：READY
- `AT+CGATT?`：已附着
- `AT+QIACT?`：获得蜂窝 IP
- `QHTTPGET/QHTTPREAD`：可拉取服务器 manifest 和固件包

## 3. 服务器设备管理显示

服务器 `ota_server.py` 与管理页面 `html/ota_manager.html` 已支持并展示以下字段：

| 字段 | 含义 |
| --- | --- |
| `networkMode` | 当前选择的通信模式，例如 `wifi` 或 `4g` |
| `uplink` | 实际上报链路，例如 `wifi` 或 `cellular` |
| `modeSelectGpio` | 模式选择 GPIO，当前为 `6` |

设备管理页面在设备详情中新增“通信链路”显示，便于区分同一设备当前通过 Wi-Fi 还是 4G 上报。

## 4. 分区表与 OTA 空间策略

当前板载 Flash 为 16MB，`v1.3.0` 使用自定义分区表 `partitions_4m_ota.csv`，重点是把 factory、ota_0、ota_1 都扩大到 4MB。

| 分区 | 偏移 | 大小 | 用途 |
| --- | --- | --- | --- |
| `nvs` | `0x9000` | `0x6000` | Wi-Fi/NVS 配置 |
| `otadata` | `0xf000` | `0x2000` | OTA 启动选择数据 |
| `phy_init` | `0x11000` | `0x1000` | PHY 初始化 |
| `factory` | `0x20000` | `0x400000` | 串口全量烧录默认 app |
| `ota_0` | `0x420000` | `0x400000` | OTA app 槽 0 |
| `ota_1` | `0x820000` | `0x400000` | OTA app 槽 1 |
| `storage` | `0xc20000` | `0x3e0000` | 预留数据区 |

当前 `v1.3.0` app 大小约 `987584` 字节，最小 app 分区为 `0x400000` 字节，剩余约 76%，后续有较大的功能扩展空间。

重要迁移约束：

- 分区表改变不能只靠普通 OTA 安全迁移。
- 从旧分区表迁移到本方案时，必须先通过串口完整烧录 bootloader、partition table、otadata 和 factory app。
- 完成一次全量烧录后，后续同分区表版本可以继续走 Wi-Fi OTA 或 4G OTA。

## 5. OTA 发布与升级策略

### 5.1 固件发布

服务器统一使用：

```bash
POST /api/releases
```

上传 `.bin` 后服务器会自动生成：

- `firmware/manifest.json`
- `firmware/version.txt`
- 固件记录
- 当前发布版本标记

`v1.3.0` 当前发布固件：

- 文件：`radar_oled_ota_v1.3.0.bin`
- 版本：`v1.3.0`
- 大小：`987584` 字节
- SHA256：`1072ae57a3b05bcdb2d11bdd1c118b4c131838c0e3ebf025674c54d9f8ac2926`

### 5.2 设备端 OTA 判断

设备读取 manifest 后比较当前版本与远端版本。

- 远端版本高于当前版本：下载固件、写入空闲 OTA 分区、设置启动分区、重启。
- 当前版本等于或高于远端版本：记录 `LATEST`，不重复升级。
- 下载或校验失败：记录错误状态，下一轮继续尝试。

### 5.3 推荐运维流程

1. 分区表首次迁移：使用 `COM5` 串口全量烧录 `v1.3.0`。
2. 确认 GPIO6 低电平：设备以 Wi-Fi 模式上线，设备管理页面显示 `wifi`。
3. 确认 GPIO6 高电平：设备重启后以 4G 模式上线，设备管理页面显示 `4g/cellular`。
4. 后续功能版本：保持同一分区表，通过 `/api/releases` 发布新 `.bin`。
5. 现场弱 Wi-Fi 或无 Wi-Fi：将 GPIO6 拉高，走 4G OTA。

## 6. 已验证结果

本次 `v1.3.0` 已完成：

- 编译通过。
- 串口全量烧录到 ESP32-S3 `COM5`。
- 启动日志确认加载新分区表，factory app 从 `0x20000` 启动。
- GPIO6 默认低电平时进入 Wi-Fi 模式。
- Wi-Fi 获取 IP `192.168.1.115`。
- 设备管理接口收到真实设备心跳，并显示 `networkMode=wifi`、`uplink=wifi`、`modeSelectGpio=6`。
- 服务器端单元测试通过。
- 服务器端设备管理页面已支持通信链路显示。

4G 模式依赖 GPIO6 物理拉高后重启验证。4G AT 联网和 4G OTA 下载写入能力已在前序 EC801E 测试中验证通过。
