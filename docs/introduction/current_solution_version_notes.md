# ESP32-S3 OLED Radar OTA 与雷达绑定平台当前方案版本说明

更新时间：2026-04-23  
当前固件版本：`v1.2.4-radar`  
OTA 服务器地址：`http://116.62.218.129:8080/`  
雷达绑定平台地址：`http://116.62.218.129:8081/`

## 1. 当前方案概览

当前系统由三部分组成：

1. ESP32-S3 端固件  
   负责 OLED 显示、UART1 Modbus-RTU 雷达轮询、Wi-Fi 联网、OTA 升级、OTA 设备心跳、雷达绑定平台数据上报。

2. OTA 固件服务器  
   部署在阿里云服务器 `/root/esp32-ota-server`，负责固件上传、版本元数据、`manifest.json`、设备心跳、升级记录和 OTA 管理页面。

3. 雷达地址绑定平台  
   部署在阿里云服务器 `/root/radar-address-binding-platform`，负责停车位表格、雷达地址记录、车位绑定、状态颜色推导、Modbus/RS485 工具台和雷达遥测展示。

当前已验证闭环：

- ESP32-S3 运行 `v1.2.4-radar`
- 雷达 Modbus 地址已从默认 `0x01` 成功改为 `0x02`
- ESP32 通过地址 `0x02` 持续读取距离
- ESP32 每分钟向 OTA 服务器上报设备状态
- ESP32 每分钟向雷达绑定平台上报雷达距离
- 雷达绑定平台中 `I-A` 车位绑定 `esp32s3-radar-02`
- 当前阈值规则下，距离约 `364-368mm` 时判定为“有车占用”

## 2. 本地 ESP32 固件工程

### 2.1 工程根目录

ESP32-S3 OTA 固件工程位于：

`E:\IOT\OTA\radar_oled_ota`

这是当前实际用于构建 `v1.2.4-radar` 的 ESP-IDF 工程。

### 2.2 关键文件清单

| 文件 | 作用 |
| --- | --- |
| `E:\IOT\OTA\radar_oled_ota\CMakeLists.txt` | ESP-IDF 工程入口，定义 `PROJECT_VER`，当前为 `v1.2.4-radar` |
| `E:\IOT\OTA\radar_oled_ota\sdkconfig` | ESP-IDF 配置，包括 ESP32-S3、16MB Flash、8MB PSRAM、双 OTA 分区、HTTP OTA 等 |
| `E:\IOT\OTA\radar_oled_ota\main\CMakeLists.txt` | main 组件构建配置，声明源文件 |
| `E:\IOT\OTA\radar_oled_ota\main\app_config.h` | 全局配置，包括 Wi-Fi、OTA URL、雷达寄存器、GPIO、上报地址 |
| `E:\IOT\OTA\radar_oled_ota\main\main.c` | 固件入口，初始化 NVS、网络栈、OLED/雷达应用、OTA 服务 |
| `E:\IOT\OTA\radar_oled_ota\main\radar_oled_app.c` | OLED 驱动、UART1 Modbus 读距离、雷达地址迁移、显示任务、雷达轮询任务 |
| `E:\IOT\OTA\radar_oled_ota\main\radar_oled_app.h` | 雷达/OLED 状态快照结构与对外接口 |
| `E:\IOT\OTA\radar_oled_ota\main\ota_service.c` | Wi-Fi 连接、manifest 拉取、版本比较、OTA 下载、分区确认 |
| `E:\IOT\OTA\radar_oled_ota\main\ota_service.h` | OTA 服务对外接口 |
| `E:\IOT\OTA\radar_oled_ota\main\device_service.c` | OTA 设备心跳、雷达绑定平台上报、HTTP JSON POST/PUT |
| `E:\IOT\OTA\radar_oled_ota\main\device_service.h` | 设备心跳服务接口 |

### 2.3 当前固件核心配置

当前配置文件：

`E:\IOT\OTA\radar_oled_ota\main\app_config.h`

关键配置如下：

```c
#define APP_WIFI_SSID               "BYWNG"
#define APP_WIFI_PASSWORD           "SRAC2025!"

#define OTA_MANIFEST_URL            "http://116.62.218.129:8080/firmware/manifest.json"
#define DEVICE_HEARTBEAT_URL        "http://116.62.218.129:8080/api/devices/heartbeat"
#define RADAR_BINDING_RADAR_URL     "http://116.62.218.129:8081/api/radars"
#define RADAR_BINDING_SLOT_URL      "http://116.62.218.129:8081/api/slots/I-A"

#define OLED_SDA_GPIO               41
#define OLED_SCL_GPIO               42

#define RADAR_UART_TX_GPIO          17
#define RADAR_UART_RX_GPIO          18
#define RADAR_MODBUS_BAUD_RATE      115200
#define RADAR_DEFAULT_SLAVE_ID      2
#define RADAR_REGISTER_DISTANCE     0x0010
#define RADAR_REGISTER_ADDRESS      0x0015
```

### 2.4 ESP32-S3 引脚定义

| 功能 | GPIO | 说明 |
| --- | --- | --- |
| OLED SDA | `GPIO41` | I2C 数据线 |
| OLED SCL | `GPIO42` | I2C 时钟线 |
| 雷达 UART TX | `GPIO17` | ESP32 UART1 TX，连接雷达 TTL RX |
| 雷达 UART RX | `GPIO18` | ESP32 UART1 RX，连接雷达 TTL TX |

雷达连接说明：

- RS485 模块的 `D (TX)` 接 ESP32 `GPIO18`
- RS485 模块的 `R (RX)` 接 ESP32 `GPIO17`
- 当前你已经将 RS485 转为 TTL 后接入 ESP32

### 2.5 当前 Modbus-RTU 协议

协议依据：

`E:\IOT\Modbus\Modbus格式.md`

当前实际使用参数：

- 通信格式：`115200 N 8 1`
- 默认地址：`0x01`
- 当前迁移后地址：`0x02`
- 未知地址：可使用 `0xFF`
- 读距离功能码：`0x03`
- 写单寄存器功能码：`0x06`
- 距离寄存器：`0x0010`
- 地址寄存器：`0x0015`

读距离示例：

```text
01 03 00 10 00 01 85 CF
```

地址修改示例：

```text
01 06 00 15 00 02 ...
```

当前 `v1.2.4-radar` 固件启动时会执行：

1. 先尝试读取地址 `0x02`
2. 如果 `0x02` 已经可读，直接进入轮询
3. 如果 `0x02` 不可读，则尝试读取地址 `0x01`
4. 如果 `0x01` 可读，则向 `0x0015` 写入 `0x0002`
5. 写入后重新读取 `0x02` 验证迁移

串口实测日志：

```text
[RADAR] programming address 1 -> 2 via register 0x0015
[RADAR] address migration verified, id=2 distance=366 mm
[RADAR] id=2 distance=366 mm ok=1 err=0
```

### 2.6 ESP 固件任务结构

| 任务 | 文件 | 周期/触发 | 作用 |
| --- | --- | --- | --- |
| `render_display` | `radar_oled_app.c` | `250ms` | 刷新 OLED 显示 |
| `radar_poll` | `radar_oled_app.c` | `500ms` | 读取 `0x02` 雷达距离 |
| `ota_task` | `ota_service.c` | 启动后执行 | Wi-Fi 连接、拉取 manifest、OTA 升级 |
| `device_heartbeat` | `device_service.c` | `60s` | 上报 OTA 设备状态与雷达绑定平台数据 |

### 2.7 固件生成过程

固件编译使用 ESP-IDF，当前环境变量大致如下：

```powershell
$env:IDF_PATH='E:\esp-idf'
$env:IDF_PYTHON_ENV_PATH='F:\ESPIDF\Espressif\python_env\idf6.1_py3.11_env'
$env:ESP_IDF_VERSION='6.1'
```

构建命令：

```powershell
cd E:\IOT\OTA\radar_oled_ota
& 'F:\ESPIDF\Espressif\python_env\idf6.1_py3.11_env\Scripts\python.exe' 'E:\esp-idf\tools\idf.py' build
```

构建产物：

| 文件 | 说明 |
| --- | --- |
| `E:\IOT\OTA\radar_oled_ota\build\radar_oled_ota.bin` | 当前编译出的应用固件 |
| `E:\IOT\OTA\radar_oled_ota\build\radar_oled_ota_v1.2.4-radar.bin` | 发布到 OTA 服务器的带版本号固件 |
| `E:\IOT\OTA\radar_oled_ota\build\bootloader\bootloader.bin` | bootloader |
| `E:\IOT\OTA\radar_oled_ota\build\partition_table\partition-table.bin` | 分区表 |
| `E:\IOT\OTA\radar_oled_ota\build\ota_data_initial.bin` | OTA 初始数据 |

当前 `v1.2.4-radar` 固件信息：

- 文件名：`radar_oled_ota_v1.2.4-radar.bin`
- 大小：`956368` 字节
- SHA256：`6d351f61c4d611bb9afc00e5e0d3081b077ed5197bfc1d8920e8812eceb66406`
- OTA 发布 URL：`http://116.62.218.129:8080/firmware/radar_oled_ota_v1.2.4-radar.bin`

### 2.8 ESP 分区与 Bootloader

当前工程使用 ESP-IDF 默认双 OTA 分区方案：

| 分区 | Offset | Size | 用途 |
| --- | --- | --- | --- |
| `nvs` | `0x9000` | `0x4000` | Wi-Fi/NVS 数据 |
| `otadata` | `0xD000` | `0x2000` | OTA 分区选择 |
| `phy_init` | `0xF000` | `0x1000` | RF 初始化数据 |
| `factory` | `0x10000` | `0x100000` | 工厂固件 |
| `ota_0` | `0x110000` | `0x100000` | OTA Slot 0 |
| `ota_1` | `0x210000` | `0x100000` | OTA Slot 1 |

当前固件大小约 `0xe97d0`，最小 app 分区为 `0x100000`，剩余空间约 9%。

bootloader 工作方式：

1. 启动时读取 `otadata`
2. 判断当前应该启动 `factory`、`ota_0` 或 `ota_1`
3. OTA 成功后切换到新分区
4. 新固件启动后调用 `esp_ota_mark_app_valid_cancel_rollback()`
5. 确认新固件有效，避免回滚

相关代码：

`E:\IOT\OTA\radar_oled_ota\main\ota_service.c`

函数：

```c
esp_err_t ota_service_mark_running_app_valid_if_needed(void)
```

## 3. OTA 固件服务器

### 3.1 服务器目录

OTA 服务器部署在阿里云：

`/root/esp32-ota-server`

访问地址：

`http://116.62.218.129:8080/`

后端监听：

`127.0.0.1:5000`

systemd 服务：

`ota-server.service`

### 3.2 OTA 服务器关键文件

| 文件 | 作用 |
| --- | --- |
| `/root/esp32-ota-server/ota_server.py` | Flask 后端，提供固件发布、manifest、设备心跳、日志等接口 |
| `/root/esp32-ota-server/html/ota_manager.html` | OTA 管理前端页面 |
| `/root/esp32-ota-server/firmware/` | 固件发布目录 |
| `/root/esp32-ota-server/firmware/manifest.json` | ESP 启动时读取的 OTA manifest |
| `/root/esp32-ota-server/firmware/version.txt` | 当前版本文本 |
| `/root/esp32-ota-server/firmware/radar_oled_ota_v1.2.4-radar.bin` | 当前发布固件 |
| `/root/esp32-ota-server/tests/test_ota_server.py` | 后端测试 |
| `/root/esp32-ota-server/deploy/ota-server.service` | systemd 服务模板 |
| `/root/esp32-ota-server/deploy/ota_manager.conf` | Nginx 配置模板 |
| `/root/esp32-ota-server/README.md` | OTA 服务器说明 |
| `/root/esp32-ota-server/requirements.txt` | Python 依赖 |

### 3.3 OTA 服务器运行配置

systemd 服务文件：

`/etc/systemd/system/ota-server.service`

核心内容：

```ini
WorkingDirectory=/root/esp32-ota-server
ExecStart=/usr/bin/python3 /root/esp32-ota-server/ota_server.py
```

Nginx 相关配置：

- 对外端口：`8080`
- 固件 URL 前缀：`/firmware/`
- 健康检查：`/health`
- 管理页面：`/`

兼容挂载：

| 路径 | 指向 |
| --- | --- |
| `/var/www/html` | `/root/esp32-ota-server/html` |
| `/var/www/firmware` | `/root/esp32-ota-server/firmware` |

### 3.4 OTA 服务器主要 API

| API | 方法 | 作用 |
| --- | --- | --- |
| `/health` | GET | 服务健康检查 |
| `/api/releases` | POST | 上传固件并发布新版本 |
| `/api/release-metadata` | GET/POST | 获取或更新版本元数据 |
| `/api/firmwares` | GET | 获取固件列表 |
| `/api/firmwares/<filename>` | DELETE | 删除旧固件 |
| `/api/devices/heartbeat` | POST | ESP 设备心跳 |
| `/api/devices` | GET | 查看设备列表 |
| `/api/logs` | GET/DELETE | 查看或清空日志 |

### 3.5 当前 OTA 发布状态

当前 release：

```json
{
  "version": "v1.2.4-radar",
  "file": "radar_oled_ota_v1.2.4-radar.bin",
  "sha256": "6d351f61c4d611bb9afc00e5e0d3081b077ed5197bfc1d8920e8812eceb66406",
  "size": 956368,
  "url": "http://116.62.218.129:8080/firmware/radar_oled_ota_v1.2.4-radar.bin"
}
```

当前 OTA 服务状态：

```text
ota-server.service: active
health.currentRelease: v1.2.4-radar
onlineDevices: 1
```

### 3.6 固件发布流程

发布流程：

1. 本地 ESP-IDF 编译生成 `radar_oled_ota.bin`
2. 复制为带版本号的 bin 文件
3. 通过 `scp` 上传到 `/root/esp32-ota-server/firmware/`
4. 调用 `/api/releases` 发布
5. 服务器自动生成 `manifest.json`
6. ESP 重启或启动时读取 manifest
7. ESP 判断远端版本高于本地版本后执行 OTA

示例发布命令：

```bash
curl -sS -X POST \
  -F 'file=@/root/esp32-ota-server/firmware/radar_oled_ota_v1.2.4-radar.bin;filename=radar_oled_ota_v1.2.4-radar.bin' \
  -F 'version=v1.2.4-radar' \
  -F 'description=Integrate radar binding platform and migrate Modbus address 01 to 02.' \
  -F 'notes=Set real Modbus template: 115200 N 8 1, distance register 0x0010, address register 0x0015. Firmware migrates radar slave 0x01 to 0x02. ESP32 reports radar telemetry to the 8081 binding platform and binds slot I-A.' \
  http://127.0.0.1:5000/api/releases
```

## 4. 雷达地址绑定平台

### 4.1 服务器目录

雷达绑定平台部署在阿里云：

`/root/radar-address-binding-platform`

访问地址：

`http://116.62.218.129:8081/`

后端监听：

`127.0.0.1:5001`

systemd 服务：

`radar-platform.service`

### 4.2 雷达绑定平台关键文件

| 文件 | 作用 |
| --- | --- |
| `/root/radar-address-binding-platform/radar_platform.py` | Flask 后端，提供布局、雷达、Modbus 工具台和状态推导 |
| `/root/radar-address-binding-platform/html/index.html` | 前端单页管理页面 |
| `/root/radar-address-binding-platform/tests/test_radar_platform.py` | 后端测试 |
| `/root/radar-address-binding-platform/deploy/radar-platform.service` | systemd 服务模板 |
| `/root/radar-address-binding-platform/deploy/radar_binding_platform.conf` | Nginx 配置模板 |
| `/root/radar-address-binding-platform/README.md` | 平台说明 |
| `/root/radar-address-binding-platform/requirements.txt` | Python 依赖 |
| `/root/radar-address-binding-platform/IMPLEMENTATION_PLAN.md` | 实现规划 |

本地镜像目录：

`E:\IOT\Modbus\radar_binding_platform_remote`

这个目录用于本地编辑后同步到服务器：

| 本地文件 | 对应远端文件 |
| --- | --- |
| `E:\IOT\Modbus\radar_binding_platform_remote\radar_platform.py` | `/root/radar-address-binding-platform/radar_platform.py` |
| `E:\IOT\Modbus\radar_binding_platform_remote\html\index.html` | `/root/radar-address-binding-platform/html/index.html` |
| `E:\IOT\Modbus\radar_binding_platform_remote\tests\test_radar_platform.py` | `/root/radar-address-binding-platform/tests/test_radar_platform.py` |
| `E:\IOT\Modbus\radar_binding_platform_remote\README.md` | `/root/radar-address-binding-platform/README.md` |

### 4.3 雷达平台运行配置

systemd 服务文件：

`/etc/systemd/system/radar-platform.service`

Nginx 配置文件：

`/etc/nginx/conf.d/radar_binding_platform.conf`

静态目录兼容挂载：

`/var/www/radar-address-binding-platform -> /root/radar-address-binding-platform/html`

数据与日志目录：

`/var/log/radar_binding_platform/`

主要数据文件：

| 文件 | 作用 |
| --- | --- |
| `/var/log/radar_binding_platform/platform_data.json` | 停车位布局、雷达列表、配置、操作历史 |
| `/var/log/radar_binding_platform/platform_logs.json` | 平台操作日志 |
| `/var/log/radar_binding_platform/platform_server.log` | Flask 服务日志 |

### 4.4 雷达平台主要 API

| API | 方法 | 作用 |
| --- | --- | --- |
| `/health` | GET | 健康检查 |
| `/api/dashboard` | GET | 获取布局、雷达、配置、状态推导总览 |
| `/api/map` | GET | 获取停车位图 |
| `/api/layout` | GET/PUT | 获取或修改行列数 |
| `/api/settings` | GET/PUT | 获取或修改运行阈值与平台配置 |
| `/api/radars` | GET/POST | 获取或保存雷达记录 |
| `/api/radars/<id>` | PUT/DELETE | 更新或删除雷达 |
| `/api/radars/<id>/simulation` | POST | 模拟雷达数据 |
| `/api/slots/<slot_id>` | PUT | 更新车位绑定 |
| `/api/serial-profiles` | GET/PUT | 串口配置 |
| `/api/modbus-profiles` | GET/PUT | Modbus 寄存器模板 |
| `/api/modbus/discover` | POST | 地址扫描 |
| `/api/modbus/address/program` | POST | 地址编程 |
| `/api/modbus/read-registers` | POST | 读寄存器 |
| `/api/modbus/write-register` | POST | 写单寄存器 |
| `/api/modbus/poll-all` | POST | 全量轮询 |
| `/api/modbus/poll-one/<id>` | POST | 单雷达轮询 |
| `/api/logs` | GET/DELETE | 查看或清空日志 |

### 4.5 当前雷达平台配置

当前串口配置：

```json
{
  "port": "/dev/ttyUSB0",
  "baudrate": 115200,
  "bytesize": 8,
  "parity": "N",
  "stopbits": 1,
  "timeoutSec": 0.5
}
```

当前 Modbus 模板：

```json
{
  "addressRegister": 21,
  "distanceRegister": 16,
  "pollRegisterStart": 16,
  "pollRegisterCount": 1,
  "statusRegister": 0
}
```

其中：

- `21` 即十六进制 `0x0015`
- `16` 即十六进制 `0x0010`

当前状态阈值：

```json
{
  "faultMinMm": 10,
  "faultMaxMm": 300,
  "occupiedMinMm": 300,
  "occupiedMaxMm": 1000,
  "freeMinMm": 1000
}
```

含义：

| 距离范围 | 状态 |
| --- | --- |
| `< 10mm` | 异常 |
| `10-300mm` | 异常 |
| `300-1000mm` | 有车占用 |
| `>= 1000mm` | 空闲 |

当前平台状态：

- 雷达 ID：`esp32s3-radar-02`
- 雷达地址：`2`
- 绑定车位：`I-A`
- 当前距离：约 `364mm`
- 当前判定：`有车占用`

### 4.6 Modbus / RS485 工具台当前限制

雷达绑定平台的 Modbus/RS485 工具台后端代码已经实现：

- 地址扫描
- 地址编程
- 读寄存器
- 写寄存器
- 单雷达轮询
- 全量轮询

但是阿里云服务器本机没有连接真实 USB-RS485 适配器，因此直接在 `8081` 工具台上访问真实串口会返回：

```text
could not open port /dev/ttyUSB0: No such file or directory
```

这不是协议错误，而是服务器本机没有 `/dev/ttyUSB0` 设备。

当前真实硬件链路是：

```text
RS485 雷达 -> TTL 转换 -> ESP32-S3 UART1 -> Wi-Fi HTTP 上报 -> 8081 雷达绑定平台
```

所以当前生产数据应以 ESP32 上报为准。  
如果未来要让 `8081` 服务器工具台直接操作 RS485 总线，需要把 USB-RS485 适配器实际插到阿里云服务器所在机器上，或者改成“ESP32 网关转发 Modbus 命令”的模式。

## 5. 当前版本演进记录

### 5.1 v1.2.2-radar

主要能力：

- OLED 显示
- 雷达轮询
- OTA 基础升级
- OTA 设备心跳

发现问题：

- 心跳上报后雷达轮询偶发卡顿
- 心跳频率偏高
- HTTP 心跳超时沿用 OTA 超时，可能阻塞较久

### 5.2 v1.2.3-radar

主要修复：

- 心跳周期改为 `60s`
- 心跳超时改为 `3s`
- OTA 过程中跳过普通心跳
- 减少 OTA 中间态同步上报
- 降低心跳任务优先级

实测结果：

- 设备从 `v1.2.2-radar` OTA 到 `v1.2.3-radar`
- 80 秒连续串口观察未再出现明显轮询卡顿

### 5.3 v1.2.4-radar

当前版本。

主要新增：

- 默认雷达地址改为 `0x02`
- 启动时自动执行 `0x01 -> 0x02` 地址迁移
- 使用真实地址寄存器 `0x0015`
- 使用真实距离寄存器 `0x0010`
- 上报雷达数据到 `http://116.62.218.129:8081/api/radars`
- 自动绑定车位 `I-A`
- 雷达绑定平台状态阈值更新为：
  - 异常：`10-300mm`
  - 占用：`300-1000mm`
  - 空闲：`>=1000mm`

实测结果：

```text
App version: v1.2.4-radar
[RADAR] programming address 1 -> 2 via register 0x0015
[RADAR] address migration verified, id=2 distance=366 mm
[RADAR] id=2 distance=366 mm ok=1 err=0
```

服务器验证：

- OTA 平台设备记录：`appVersion = v1.2.4-radar`
- 升级记录：`v1.2.3-radar -> v1.2.4-radar`
- 雷达平台：`esp32s3-radar-02`
- 绑定车位：`I-A`
- 状态：`有车占用`

## 6. 常用运维命令

### 6.1 登录服务器

```powershell
wsl ssh ali
```

### 6.2 OTA 服务器

```bash
systemctl status ota-server.service --no-pager
systemctl restart ota-server.service
curl -sS http://127.0.0.1:8080/health
curl -sS http://127.0.0.1:8080/api/release-metadata
```

测试：

```bash
cd /root/esp32-ota-server
/usr/bin/python3 -m unittest discover -s tests -v
```

### 6.3 雷达绑定平台

```bash
systemctl status radar-platform.service --no-pager
systemctl restart radar-platform.service
curl -sS http://127.0.0.1:8081/health
curl -sS http://127.0.0.1:8081/api/dashboard
```

测试：

```bash
cd /root/radar-address-binding-platform
/usr/bin/python3 -m unittest discover -s tests -v
```

### 6.4 Nginx

```bash
nginx -t
systemctl reload nginx
```

### 6.5 查看端口

```bash
ss -lntp | grep -E '8080|8081|5000|5001'
```

## 7. 当前验证状态

### 7.1 服务状态

| 服务 | 状态 |
| --- | --- |
| `ota-server.service` | `active` |
| `radar-platform.service` | `active` |
| `http://116.62.218.129:8080/health` | 正常 |
| `http://116.62.218.129:8081/health` | 正常 |

### 7.2 固件状态

| 项目 | 当前值 |
| --- | --- |
| 固件版本 | `v1.2.4-radar` |
| OTA manifest 版本 | `v1.2.4-radar` |
| 雷达地址 | `0x02` |
| 轮询寄存器 | `0x0010` |
| 地址寄存器 | `0x0015` |
| 轮询周期 | `500ms` |
| 心跳周期 | `60s` |

### 7.3 雷达平台状态

| 项目 | 当前值 |
| --- | --- |
| 雷达 ID | `esp32s3-radar-02` |
| 雷达地址 | `2` |
| 绑定车位 | `I-A` |
| 当前距离 | 约 `364-368mm` |
| 当前状态 | `有车占用` |
| 阈值规则 | `10-300 异常 / 300-1000 占用 / >=1000 空闲` |

## 8. 后续建议

### 8.1 ESP 固件侧

建议下一步将雷达绑定平台上报从 `device_service.c` 中拆成独立模块，例如：

- `binding_service.c`
- `binding_service.h`

这样 OTA 心跳和雷达平台上报职责会更清晰。

### 8.2 雷达绑定平台侧

建议后续增加一个“ESP 网关模式”：

1. 页面点击“读寄存器”
2. 服务器向 ESP32 下发 HTTP 命令
3. ESP32 通过 UART1 执行 Modbus
4. ESP32 将结果返回服务器

这样就不需要阿里云服务器本机插 USB-RS485。

### 8.3 硬件侧

如果未来有多颗雷达，建议：

- 每颗新雷达先单独接入
- 使用 `0x01 -> 新地址` 编址
- 编址后贴标签
- 再并入 RS485 总线
- 平台中建立对应车位绑定

## 9. 一句话总结

当前系统已经从“单个 OLED 雷达显示 Demo”演进为一个具备 OTA、远程设备心跳、雷达地址迁移、车位绑定平台和状态可视化的完整原型系统。`v1.2.4-radar` 是当前推荐基线版本。
