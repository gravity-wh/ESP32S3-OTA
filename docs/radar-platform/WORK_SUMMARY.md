# Modbus 雷达通信项目工作整理

## 1. 项目目标
- 通过串口转 TCP 设备，与雷达进行 Modbus 通信验证。
- 提供可持续轮询读取与参数配置能力。
- 支持修改雷达地址（Unit ID）。
- 提供一键部署方式，便于现场快速使用。

## 2. 已完成内容

### 2.1 通信验证脚本
- 新建 `check_modbus_tcp.py`。
- 支持读取 `radar_ip.txt` 中的 IP 与端口。
- 默认端口策略：
  - 优先读取 `radar_ip.txt` 的端口字段。
  - 若未配置端口，回退到 `4196`。
- 支持 FC03 读取保持寄存器（单次读取）。
- 支持轮询模式（`--watch`），可配置间隔（如每 2 秒）。

### 2.2 现场参数适配
- 根据实际设备将目标改为 `192.168.1.200:4196`。
- 修复因默认端口 `502` 导致的连接拒绝问题（WinError 10061）。
- 实测通过示例：
  - `Target: 192.168.1.200:4196 ...`
  - `[PASS] OK | unit=1 function=3 registers=[2]`

### 2.3 功能扩展（持续读取）
- 按需求实现地址 `0010`（按十六进制即 `16`）轮询读取。
- 每 2 秒更新一次读取值。
- 输出包含时间戳与 PASS/FAIL 状态，便于排障。

### 2.4 完整应用开发（GUI）
- 新建 `radar_modbus_app.py`（Tkinter 桌面应用）。
- 支持连接参数设置：
  - IP、端口、Unit ID、超时。
- 支持读取参数设置：
  - 查询地址、读取数量、轮询时间。
- 支持操作：
  - 单次读取（FC03）
  - 开始/停止轮询（FC03）
  - 修改雷达地址（FC06 写单寄存器）
- 支持日志窗口显示结果与错误信息。
- 修改地址成功后自动切换当前 Unit ID。

### 2.5 一键部署
- 新建 `build.bat`。
- 自动安装/更新 `pyinstaller` 并打包生成 EXE。
- 输出文件：
  - `dist\RadarModbusApp.exe`

## 3. 主要文件说明
- `check_modbus_tcp.py`：命令行通信检测与轮询脚本。
- `radar_modbus_app.py`：图形化完整应用。
- `build.bat`：一键打包脚本。
- `radar_ip.txt`：现场 IP/端口配置文件。
- `README.txt`：运行、部署、参数说明文档。

## 4. 关键命令

### 4.1 命令行单次读取
```powershell
python check_modbus_tcp.py --ip-file radar_ip.txt --unit-id 1 --address 16 --count 1
```

### 4.2 命令行轮询（2秒）
```powershell
python check_modbus_tcp.py --ip-file radar_ip.txt --unit-id 1 --address 16 --count 1 --watch --interval 2
```

### 4.3 启动图形应用
```powershell
python radar_modbus_app.py
```

### 4.4 一键打包
```powershell
.\build.bat
```

## 5. 验证记录
- 脚本参数检查通过：`python check_modbus_tcp.py --help`
- 语法检查通过：`python -m py_compile radar_modbus_app.py check_modbus_tcp.py`
- 现场通信验证通过（`192.168.1.200:4196`，FC03 可读）。

## 6. 注意事项
- 雷达地址修改的“地址寄存器”必须以设备协议手册为准。
- 手册中的地址 `0010` 可能有十进制/十六进制差异：
  - 若十六进制 `0x0010`，参数用 `--address 16`。
  - 若十进制 `0010`，参数用 `--address 10`。
- 修改雷达地址后，后续访问需使用新 Unit ID。

## 7. 后续建议
1. 增加 CSV/数据库日志持久化。
2. 增加地址修改后的自动读回校验。
3. 增加多设备批量轮询与状态看板。
