# radar_oled_ota

这是面向 ESP32-S3 的 OLED + Modbus 雷达 + Wi-Fi OTA 工程，目录位于 `E:\IOT\OTA\radar_oled_ota`。

## 功能概览

- OLED 显示雷达距离、Wi-Fi 状态、OTA 状态和当前应用版本
- UART1 通过 `GPIO17(TX)` / `GPIO18(RX)` 轮询 Modbus RTU 雷达
- Wi-Fi STA 自动连接 `BYWNG`
- 从 `http://116.62.218.129:8080/firmware/manifest.json` 拉取 OTA 清单
- 使用双 OTA 分区和 bootloader 回滚机制
- OLED 按 y 轴镜像显示，修正原本方向错误问题

## 引脚定义

- OLED I2C SCL: `GPIO12`
- OLED I2C SDA: `GPIO11`
- 雷达 UART1 TX: `GPIO17`
- 雷达 UART1 RX: `GPIO18`

## OTA 架构

- 分区表使用 `two_ota`
- `factory` 作为首个可启动应用区
- `ota_0` / `ota_1` 用于后续在线升级
- bootloader 开启 `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`
- 新固件首次启动后，由应用调用 `esp_ota_mark_app_valid_cancel_rollback()` 确认镜像有效

当前固件版本定义在顶层 `CMakeLists.txt`：

```cmake
set(PROJECT_VER "v1.2.0-radar")
```

## 当前 OTA 服务器说明

- 固件目录：`http://116.62.218.129:8080/firmware/`
- 清单地址：`http://116.62.218.129:8080/firmware/manifest.json`
- 当前远端清单版本：`v1.1-ota`

由于本工程本地版本号为 `v1.2.0-radar`，默认比较逻辑会认为远端版本更旧，因此不会自动升级。这是安全设计，避免把新工程误降级成旧的测试镜像。如果要验证 OTA，请先把服务器上的 `manifest.json` 和固件版本更新到更高版本号。

## 构建与烧录

在 ESP-IDF 环境中执行：

```powershell
idf.py build
idf.py -p COM5 flash monitor
```

或者仅烧录：

```powershell
idf.py -p COM5 flash
```

## 调试观察点

- 串口日志中会持续输出雷达距离
- OLED 第 1 行显示 Wi-Fi 状态
- OLED 第 2 行显示距离值
- OLED 第 3 行显示 Modbus / OTA 简要状态
- OLED 第 4 行显示应用版本

如果需要继续做 OTA 联调，建议下一步把服务器上的 `manifest.json` 指向本工程生成的 `radar_oled_ota.bin`，并把版本号提升到 `v1.2.1` 或更高。
