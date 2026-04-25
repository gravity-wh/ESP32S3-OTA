# ESP32-S3 Radar OLED OTA Architecture Introduction

## 1. 项目目标

本项目由两部分组成：

1. `ESP32-S3` 端应用固件  
   负责 Wi-Fi 联网、OLED 显示、Modbus RTU 轮询雷达距离、向 OTA 服务器上报设备心跳，以及在发现新版本后执行 OTA 升级。
2. OTA 固件服务器  
   负责固件发布、`manifest.json` 生成、历史固件管理、设备在线状态记录、升级结果记录，以及网页管理后台。

本次稳定版本为 `v1.2.3-radar`。这个版本重点修复了 `v1.2.2` 中“心跳上报后雷达轮询偶发卡顿”的问题，并已在实际设备 `COM5` 对应的 ESP32-S3 上完成 OTA 验证。

## 2. 当前生产环境

### 2.1 OTA 服务器生产目录

当前生产项目根目录：

`/root/esp32-ota-server`

关键文件与目录：

- `ota_server.py`：Flask 后端主程序
- `html/ota_manager.html`：管理后台前端页面
- `firmware/`：固件文件、`manifest.json`、`version.txt`
- `tests/test_ota_server.py`：接口测试
- `deploy/ota-server.service`：systemd 服务模板
- `deploy/ota_manager.conf`：Nginx 配置模板

### 2.2 生产服务拓扑

生产环境采用以下结构：

1. Nginx 监听 `8080`
2. Nginx 反向代理 Flask 应用
3. Flask 进程由 systemd 托管
4. 固件文件通过 `firmware/` 目录对外发布
5. ESP 设备通过 HTTP 访问 `manifest.json` 与 `.bin`

对外地址：

- 管理与下载入口：`http://116.62.218.129:8080/`
- 固件清单：`http://116.62.218.129:8080/firmware/manifest.json`
- 设备心跳接口：`http://116.62.218.129:8080/api/devices/heartbeat`

### 2.3 systemd 与 Nginx

当前 systemd 服务文件：

`/etc/systemd/system/ota-server.service`

核心配置：

- `WorkingDirectory=/root/esp32-ota-server`
- `ExecStart=/usr/bin/python3 /root/esp32-ota-server/ota_server.py`

为了兼容原有 Nginx 路径，服务器保留了以下 bind mount 入口：

- `/var/www/html -> /root/esp32-ota-server/html`
- `/var/www/firmware -> /root/esp32-ota-server/firmware`

这意味着：

- 源码维护位置已经切到 `/root/esp32-ota-server`
- Nginx 依旧可以通过旧路径读页面和固件
- 重启后 mount 关系仍然有效

## 3. OTA 服务器设计

### 3.1 后端职责

服务器后端由 `ota_server.py` 实现，采用 Flask 单体服务模式，核心能力包括：

- 统一发布固件：`POST /api/releases`
- 兼容旧上传方式：`POST /upload` 与 `POST /api/upload`
- 设备心跳记录：`POST /api/devices/heartbeat`
- 获取固件列表：`GET /api/firmwares`
- 删除旧固件：`DELETE /api/firmwares/<filename>`
- 发布信息读取：`GET /api/release-metadata`
- 发布信息兼容写入：`POST /api/release-metadata`
- 设备列表：`GET /api/devices`
- 健康检查：`GET /health`

### 3.2 数据存储方式

服务器当前不依赖数据库，使用 JSON 文件持久化：

- `ota_data.json`：设备、固件、当前发布元数据
- `ota_logs.json`：操作日志
- `ota_server.log`：服务日志

这种设计的优点：

- 部署简单
- 备份方便
- 易于人工检查与修复
- 对当前设备规模足够

限制也很明确：

- 不适合高并发
- 不适合大量设备的复杂检索
- 不具备事务与细粒度权限模型

对当前“少量 ESP 设备 + 轻量管理后台”的场景，这种实现是合理且高性价比的。

### 3.3 固件发布模式

当前推荐发布模式为统一发布接口：

`POST /api/releases`

一次上传同时完成：

1. 保存 `.bin`
2. 自动计算 `sha256`
3. 自动生成 `manifest.json`
4. 自动更新 `version.txt`
5. 自动刷新当前发布版本信息
6. 自动记录固件描述与变更说明

前端支持两类入口：

- 网页拖拽上传
- API 直接上传

这样做的好处是：

- 前端和自动化脚本走同一条发布链路
- 避免“bin 已更新但 manifest 没同步”的人工错误
- 版本说明、文件名、SHA256、下载 URL 始终一致

### 3.4 manifest 结构

当前 `manifest.json` 的核心字段包括：

- `version`
- `file`
- `url`
- `size`
- `sha256`
- `description`
- `notes`

ESP 端只要能拿到 `version` 和 `url`，就能判断是否有新版本并执行升级；而 `sha256`、`description`、`notes` 则为后台展示、审计和后续完整性校验保留了扩展能力。

### 3.5 设备管理设计

ESP 设备通过 `POST /api/devices/heartbeat` 上报：

- `id`
- `name`
- `mac`
- `ip`
- `status`
- `firmware`
- `appVersion`
- `tags`
- `telemetry`
- `ota`

其中 `ota` 字段承载升级状态：

- `state`
- `result`
- `message`
- `fromVersion`
- `toVersion`

服务器会根据心跳自动维护：

- `lastSeen`
- `lastUpdate`
- `lastOtaCheckAt`
- `lastUpgradeAt`
- `lastUpgradeFrom`
- `lastUpgradeTo`
- `online`

当前在线判断超时阈值为 `120` 秒。设备超过该时间未上报，即会被视为离线。

### 3.6 当前版本的发布记录

已经成功发布并验证：

- `v1.2.3-radar`
- 文件名：`radar_oled_ota_v1.2.3-radar.bin`
- SHA256：`a488c56422388c6bcaa6d685a85609b46658b78a712f95cbb2df8999b8de4d21`
- 文件大小：`954400` 字节

## 4. ESP32-S3 固件 OTA 设计

### 4.1 固件职责

ESP 固件由以下几个逻辑模块组成：

- `main.c`：系统入口
- `ota_service.c`：联网、拉取 manifest、执行 OTA、标记当前分区有效
- `device_service.c`：设备心跳上报
- `radar_oled_app.c`：OLED 驱动、UART Modbus 轮询、状态快照、USB 日志输出
- `app_config.h`：系统常量与 GPIO 定义

### 4.2 启动流程

设备上电或重启后的执行顺序如下：

1. Bootloader 从 flash 启动
2. 读取分区表
3. 根据 `otadata` 选择 factory / `ota_0` / `ota_1`
4. 加载应用镜像
5. 应用启动后初始化 NVS、网络栈、事件循环
6. 调用 `ota_service_mark_running_app_valid_if_needed()`
7. 初始化 OLED、UART、USB Serial JTAG
8. 启动显示任务与雷达轮询任务
9. 启动 OTA 任务
10. OTA 任务联网后执行版本检查

### 4.3 Bootloader 与回滚机制

当前工程启用了：

- 双 OTA 分区
- Bootloader 应用回滚支持

`sdkconfig` 中可见：

- `CONFIG_PARTITION_TABLE_TWO_OTA=y`
- `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`

这意味着：

1. 新固件下载到当前运行分区之外的 OTA 分区
2. Bootloader 将 `otadata` 指向新分区
3. 新固件首次启动时处于待确认状态
4. 应用启动后调用 `esp_ota_mark_app_valid_cancel_rollback()`
5. 若应用未成功确认，系统可在后续回滚

当前应用中，这一步由 `ota_service_mark_running_app_valid_if_needed()` 完成。

### 4.4 当前分区表

从实际串口启动日志可见，当前 ESP32-S3 分区表如下：

| Label | 用途 | Offset | Length |
| --- | --- | --- | --- |
| `nvs` | Wi-Fi/NVS 数据 | `0x9000` | `0x4000` |
| `otadata` | OTA 选择信息 | `0xD000` | `0x2000` |
| `phy_init` | RF 初始化数据 | `0xF000` | `0x1000` |
| `factory` | 工厂应用分区 | `0x10000` | `0x100000` |
| `ota_0` | OTA 应用槽 0 | `0x110000` | `0x100000` |
| `ota_1` | OTA 应用槽 1 | `0x210000` | `0x100000` |

设备硬件能力也已在串口日志中验证：

- SPI Flash：`16MB`
- PSRAM：`8MB`

### 4.5 OTA 检查流程

ESP 固件的 OTA 任务流程如下：

1. 连接 Wi-Fi
2. 请求 `manifest.json`
3. 读取 `version` 与 `url`
4. 将远端版本与本地 `app_desc->version` 比较
5. 如果远端版本更高，则进入 OTA 下载流程
6. 使用 `esp_https_ota` 写入目标分区
7. 写入成功后重启
8. 新固件启动后标记当前 app 有效

当前配置：

- Manifest 地址：`http://116.62.218.129:8080/firmware/manifest.json`
- OTA 检查周期：`30000 ms`
- OTA HTTP 超时：`20000 ms`

说明：

- 这里使用的是 HTTP，而不是 HTTPS
- 因此工程启用了 `CONFIG_ESP_HTTPS_OTA_ALLOW_HTTP=y`
- 这适合当前内网/受控环境调试与轻量部署
- 若未来面向公网大规模部署，建议切换到 HTTPS 与证书校验

### 4.6 版本比较策略

版本比较函数不是简单字符串比较，而是按数字段提取比较，例如：

- `v1.2.3-radar > v1.2.2-radar`

这样可以避免：

- `v1.2.10` 被错误判定小于 `v1.2.9`

### 4.7 设备心跳设计

设备心跳服务负责向服务器上报：

- 当前固件版本
- 在线状态
- 雷达距离
- 轮询成功次数
- 轮询失败次数
- OTA 状态

当前配置：

- 心跳地址：`http://116.62.218.129:8080/api/devices/heartbeat`
- `v1.2.3` 心跳周期：`60000 ms`
- `v1.2.3` 心跳超时：`3000 ms`

### 4.8 v1.2.2 卡顿问题原因分析

`v1.2.2` 的现象是：设备在心跳后偶尔停顿一段时间，不再持续执行雷达轮询。

排查后，主要原因有三点：

1. 心跳过于频繁  
   旧版本每 `15` 秒发一次心跳，网络波动时会更容易打断系统节奏。
2. 心跳 HTTP 超时太长  
   心跳沿用了 OTA 的 `20000 ms` 超时，最坏情况下一个心跳会占用很长时间。
3. OTA 状态上报过于同步  
   在 OTA 检查、准备、下载等阶段存在多次同步即时上报，造成网络任务与串口轮询在时间上更容易互相挤占。

### 4.9 v1.2.3 修复策略

`v1.2.3-radar` 已做以下修复：

1. 将心跳周期从 `15000 ms` 调整为 `60000 ms`
2. 将心跳请求超时单独缩短为 `3000 ms`
3. Wi-Fi 未连上时不发送心跳
4. OTA 进行中不发送普通心跳
5. 心跳任务启动后先等待一个周期，再发送首次心跳
6. 将心跳任务优先级降低到 `2`
7. 删除大部分 OTA 过程中的同步即时上报，仅保留关键状态

这套调整的目标不是“完全不发心跳”，而是让心跳成为低干扰、低频、可容错的后台任务。

### 4.10 OTA 实测结果

已对真实设备执行 OTA 验证，过程如下：

1. 设备原版本为 `v1.2.2-radar`
2. 服务器发布 `v1.2.3-radar`
3. 设备启动后读取 manifest，发现远端版本更高
4. 成功下载并写入新分区
5. 自动重启
6. 启动进入 `v1.2.3-radar`
7. 再次检查 manifest，确认当前已是最新版本

设备管理接口同步记录到：

- `lastUpgradeFrom = v1.2.2-radar`
- `lastUpgradeTo = v1.2.3-radar`
- `lastUpgradeAt = 2026-04-22 15:07:20`

## 5. 前端管理后台设计

管理后台页面支持：

- 拖拽上传固件
- 录入版本号、固件描述、更新说明
- 查看当前发布版本
- 查看固件列表
- 删除旧固件
- 查看设备在线状态
- 查看设备最近升级结果
- 展开查看固件介绍

前端和后端都围绕统一发布接口工作，因此页面发布与脚本发布的结果完全一致。

## 6. 运维建议

### 6.1 发布建议

建议每次发布时遵循以下顺序：

1. 本地完成编译
2. 核对 `PROJECT_VER`
3. 保留带版本号的产物文件名
4. 通过 `/api/releases` 上传
5. 检查 `/api/release-metadata`
6. 检查 `/health`
7. 观察设备是否自动 OTA
8. 在 `/api/devices` 中确认升级结果

### 6.2 安全建议

当前服务器已可稳定运行，但若未来投入更广泛环境，建议继续增强：

- 将固件下载切换到 HTTPS
- 对发布接口增加鉴权
- 对设备心跳增加 token 或签名
- 使用数据库替代纯 JSON 文件
- 为每台设备建立唯一设备密钥
- 将 `sha256` 校验真正落到 ESP 端流程

### 6.3 容量建议

当前应用镜像约 `954 KB`，每个 OTA 分区 `1 MB`，空间已经比较紧凑。后续继续迭代时要特别注意：

- 不要无控制地引入大型组件
- 保持日志与资源文件轻量
- 必要时升级到更大的 OTA 分区布局

## 7. 总结

当前 OTA 方案具备以下特点：

- 服务器结构简单、易运维
- 发布链路统一，前后端一致
- 设备具备在线状态与升级历史可视化
- ESP32-S3 采用双 OTA 分区与回滚保护
- OLED 显示、雷达轮询、设备心跳、OTA 升级已经集成为一个完整应用
- `v1.2.3-radar` 已修复 `v1.2.2` 中心跳导致雷达轮询卡顿的问题，并完成实机 OTA 验证

对于当前这套 Radar + OLED + OTA 的产品原型来说，这已经是一套可继续稳定演进的基础架构。
