# ESP32-S3 多雷达边缘网关与 OTA 策略 v1.3.2

## 硬件连接

- 主控：ESP32-S3 N16R8，USB Serial/JTAG 调试口为 COM5。
- OLED：I2C0，SDA=GPIO41，SCL=GPIO42，保留原有 OLED 显示功能。
- RS485 雷达总线：UART1，TX=GPIO17，RX=GPIO18，默认 115200 baud。所有雷达挂在同一 RS485 总线上。
- 4G 模块：EC801E/AT 模块，UART2，ESP32 TX=GPIO4，ESP32 RX=GPIO5，默认 115200 baud。
- GPIO6 选择 Wi-Fi/4G 的功能已取消。当前固定策略是 Wi-Fi 做数据上报与下行命令轮询，4G 做 OTA 包/manifest 传输。

## 多雷达启动编址

- 每次启动先扫描已有地址 `02..32`，把可响应雷达加入队列。
- 再探测地址 `01`。如果发现新雷达，将其改写到当前队列尾部，例如已有 `02` 时改为 `03`。
- 如果发现队列地址不是从 `02` 开始连续递增，则反复把当前最大地址改到从 `02` 开始的第一个空位，直到队列变成 `02,03,04...`。
- 初始化完成后进入轮询状态，按队列从 `02` 到最大地址依次读取距离。
- OLED 第一行显示 `ID02` 的距离，第二行显示占用数/总车位数，例如 `OCC 2/3 ID02`。

## 数据上报

- Wi-Fi 连接成功后，ESP32 向 OTA 平台 `http://116.62.218.129:8080/api/devices/heartbeat` 上报设备心跳。
- 同一份心跳包含 `telemetry.radars[]`，每个雷达包含地址、距离、在线状态、占用判断、轮询成功/失败计数。
- ESP32 同时向雷达平台 `http://116.62.218.129:8081/api/edge/heartbeat` 上报边缘雷达快照。
- 雷达平台根据 `edgeDeviceId` 展示边缘侧雷达，避免误认为雷达直接接在云服务器上。

## 云端下行链路

- 云端创建命令：`POST /api/edge/devices/<deviceId>/commands`。
- ESP32 周期轮询：`GET /api/edge/devices/<deviceId>/commands`。
- ESP32 在本地 RS485 总线上执行命令，再回传：`POST /api/edge/devices/<deviceId>/commands/<commandId>/result`。
- 已支持动作：`read_distance`、`read_registers`、`write_register`、`change_address`。
- 实测命令 `read_distance address=2` 已由设备 `esp32s3-aca704269d60` 执行成功，并回传距离结果。

## OTA 策略

- 分区表面向 16MB Flash：`factory`、`ota_0`、`ota_1` 各 4MB，SPIFFS 约 3.875MB。
- 当前 v1.3.2 固件大小约 0.94MB，最小 App 分区剩余约 77%，为后续 OTA 功能保留了足够空间。
- Wi-Fi 不下载 OTA 包，只负责设备在线、遥测、命令轮询和 OTA 状态上报。
- 4G 模块通过 PDP 上网，使用 Quectel QHTTP 流程获取 OTA manifest 和固件包。
- 已验证 EC801E：`AT` 正常、SIM READY、`CSQ=31`、`CGATT=1`、`QIACT` 获取 `10.18.134.159`，并可通过 4G 获取 OTA manifest。
- 最终发布版本：`v1.3.2`，文件 `radar_oled_ota_v1.3.2.bin`，SHA256 `2e3e77c324e9678113ca1e65f5d4c5ec7a8c3062319887c48e18db85fbbb935f`。

## 验证记录

- ESP-IDF 环境：`E:\esp-idf`，工具目录 `E:\esp-idf-tools`，Python `idf6.1_py3.12_env`。
- 本地构建目录：`E:\IOT\OTA\radar_oled_ota\build_v132`。
- 已烧录 COM5 并启动 `v1.3.2`。
- 启动扫描实测发现 `ID02`、`ID03` 两个雷达，队列 `count=2`。
- 8080 OTA 平台当前版本为 `v1.3.2`。
- 8081 雷达平台已部署边缘设备、边缘雷达和下行命令 UI/API。
