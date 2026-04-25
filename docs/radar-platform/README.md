# 雷达地址绑定平台

项目目录：`/root/radar-address-binding-platform`

访问地址：`http://116.62.218.129:8081/`

## 核心能力
- 停车位网格编辑：行标签为罗马数字，列标签为 Excel 风格字母
- 车位启用 / 禁用 / 雷达地址绑定
- 单元格状态自动着色：未启用、待绑定、待确认、异常、有车、空闲
- RS485 / Modbus-RTU 地址扫描、地址编程、寄存器读写、全量轮询
- 模拟模式：无硬件时先联调前后端与 API
- 可配置阈值、串口参数、寄存器模板

## 目录结构
- `radar_platform.py`：Flask 后端
- `html/index.html`：前端管理页面
- `tests/test_radar_platform.py`：后端接口测试
- `deploy/radar-platform.service`：systemd 服务文件
- `deploy/radar_binding_platform.conf`：Nginx 配置文件
- `requirements.txt`：Python 依赖

## 部署说明
- Flask 后端监听：`127.0.0.1:5001`
- Nginx 对外监听：`8081`
- 静态目录兼容挂载：`/var/www/radar-address-binding-platform -> /root/radar-address-binding-platform/html`
- 日志目录：`/var/log/radar_binding_platform/`

## 常用命令
```bash
systemctl status radar-platform.service --no-pager
systemctl restart radar-platform.service
nginx -t && systemctl reload nginx
/usr/bin/python3 -m unittest discover -s /root/radar-address-binding-platform/tests -v
curl -sS http://127.0.0.1:5001/health
curl -sS http://127.0.0.1:8081/health
```

## 硬件接入说明
- 默认使用模拟模式，可先验证前后端逻辑
- 若接入真实 RS485 雷达：
  1. 安装 `pyserial`
  2. 在页面中填写串口参数（如 `/dev/ttyUSB0`, 115200, N, 8, 1）
  3. 按雷达协议修改地址寄存器 / 距离寄存器 / 状态寄存器
  4. 关闭“模拟模式”后即可使用真实总线轮询

## 当前实际雷达协议模板
- 通信格式：115200, N, 8, 1
- 默认从机地址：0x01
- 未知从机地址：0xFF
- 距离寄存器：0x0010
- 地址寄存器：0x0015
- 读取距离：功能码 0x03，读取 1 个寄存器
- 修改地址：功能码 0x06，向 0x0015 写入 0x0001 到 0x00FE
- 状态寄存器：当前协议文档未提供，默认不启用
- 占用阈值：30~100 mm
- 空闲阈值：>=100 mm

如果你的雷达协议不同，请在页面里直接修改寄存器模板与阈值，不需要再改源码。
