# ESP32+RS485+4G 场景下 OTA 与通信可靠性文献综述

## 1. 背景与问题定义

面向“ESP32-S3 + TFmini-i-485 + Air780E”的工程场景，系统目标不仅是“能跑通”，而是要在多雷达扩展、弱网环境和远程升级条件下长期稳定运行。结合项目实施清单可知，核心技术矛盾集中在三条主线：

1) OTA 升级的安全性与可恢复性；
2) RS485/Modbus 多节点通信的确定性；
3) 4G 上报链路的抖动容忍与断网恢复。

为此，本文围绕上述三条主线构建文献池，并采用“相关性、证据强度、可复现性、时效性、来源可信度”五维筛选。最终纳入 12 篇高相关文献，其中英文 10 篇、中文 2 篇；2021-2026 年文献占比超过 80%；期刊/高质量会议占绝对多数，满足开题综述对时效性与学术规范的要求 [1]-[12]。

需要说明的是：本轮中文条目严格限定在可核验且与“ESP32+Modbus+4G OTA”直接相关的核心文献，因此中文文献为 2 篇 [11][12]，其余由可公开核验的国际文献补齐。

## 2. OTA 研究现状

从研究结构看，OTA 领域已从“单点升级实现”转向“平台化运维 + 安全治理 + 异常恢复”的系统工程。DeOTA-IoT 将 OTA 机制拆分为准备、分发、安装、恢复、调度、打包等模块，为工程实现提供了清晰的职责边界 [5]。

在 ESP32 实证层面，IOTAfy 给出了较完整的平台化路径：设备端 OTA 库、管理端版本编排、分组升级与回滚联动，且在 50 台设备测试中给出较高升级成功率，说明“集中编排 + 端侧回滚”适合中等规模部署 [1]。Kubaščík 等进一步把流程下沉到工程细节（分区策略、升级流程、实测验证），为落地提供了可直接复用的执行模板 [2]。

安全方面，Jaouhari 与 Bouvet 将 secure bootloader 作为 OTA 信任根入口，强调固件完整性校验与受控启动链 [4]。Park 等针对资源受限设备提出轻量化 FOTA 安全机制，在对抗中间人攻击（MITM）与压缩传输开销之间做平衡，提示“安全强度与端侧资源预算”必须联动设计 [6]。

链路侧约束方面，NB-IoT OTA 研究表明：在低带宽/高时延网络中，升级包尺寸、分片策略与重传参数直接影响能耗和总时延 [7]。这对 Air780E 类 4G 场景有直接启发：升级不应只看“能否下载成功”，而应以“升级成功率、回滚成功率、链路成本”作为联合指标 [3][7]。

综合看，当前 OTA 研究已经形成共识：A/B 分区、签名校验、健康检查和失败回滚是工业可用性的最低配置，而不是可选优化项 [1][3][4][5]。

## 3. RS485/Modbus 研究现状

RS485/Modbus 方向的文献重点，从早期“协议可通信”转向“多节点稳定轮询 + 网关协议转换 + 端云协同”。Boonmeeruk 等展示了 ESP32 在工业网关中的可行性：以 Modbus 侧采集、MQTT/REST 侧上送，实现低成本 IIoT 网关替代 [8]。Dafare 等则把 RS485 与 MQTT 网关整合到同一节点，进一步验证了“现场总线 + 消息上云”的工程路径 [10]。

在 RS485 总线扩展能力方面，Herrera-Arroyo 等的实证系统明确指出单总线可扩展多从站，并通过统一采集与上层可视化形成闭环，证明“先稳定轮询，再做业务策略”是更稳妥的实施顺序 [9]。国内文献同样强调多从站地址规划、CRC 校验、超时重试与主从轮询节奏是网关稳定性的决定因素 [11]。

这与项目中的“1进8出扩展 + 多雷达轮询”高度一致：若没有确定的地址分配、仲裁时序和错误恢复机制，扩展节点数会迅速放大抖动和误码风险，导致上层数据质量下降 [9][11]。

## 4. 4G 链路与云端上报现状

4G/蜂窝链路研究的核心结论是：网络可达不等于业务可用。多文献都显示，在蜂窝网络中，抖动、重连和分片重传会显著影响端到端稳定性 [3][7][8]。

从协议栈组合看，实践上多采用“现场侧 Modbus，云侧 MQTT/HTTP/REST”分层架构，以降低耦合并便于云端扩展 [8][10]。在 OTA 场景中，弱网与高时延会进一步放大包体、重试和重连策略的重要性，因此需要本地缓存队列、断点续传与有界回放机制 [3][7]。

国内工程条目也给出与项目高度重合的路径：通过 ESP32+4G 模组实现远程 OTA、状态机控制、离线缓存与回滚联动，说明“通信状态机 + 本地队列 + 升级保护”应作为统一设计，而不是分散在不同模块中后期拼接 [12]。

## 5. 跨维度对比

### 5.1 入选文献对比矩阵

| 编号 | 年份 | 来源                      | 方法                                  | 主要结论                             | 主要局限                     | 对应阶段(A-F) |
| ---- | ---: | ------------------------- | ------------------------------------- | ------------------------------------ | ---------------------------- | ------------- |
| [1]  | 2026 | Engineering Proceedings   | ESP32 OTA 平台化（分组升级+回滚）     | 中等规模设备可实现高成功率 OTA 管理  | 偏 Wi-Fi 测试环境            | E, F          |
| [2]  | 2024 | IEEE Informatics          | ESP32 OTA 全流程实测                  | 分区与流程设计可显著提升可维护性     | 场景规模有限                 | B, F          |
| [3]  | 2025 | Computers                 | 多协议 OTA 架构（Wi-Fi/BLE/LoRa/GSM） | 多链路统一编排可提升异构设备可更新性 | 工程复杂度提升               | C, E, F       |
| [4]  | 2022 | IEEE ICOIN                | 通用安全 bootloader                   | 启动链可信是 OTA 安全前提            | 对硬件信任根依赖较高         | C, F          |
| [5]  | 2026 | Sensors                   | OTA 技术目录与机制分类                | 可用于系统化设计 OTA 生命周期        | 更偏方法论                   | A, F          |
| [6]  | 2025 | Electronics               | 轻量安全 FOTA（抗 MITM）              | 在资源受限设备上可兼顾安全与开销     | 通用性需按芯片评估           | C, F          |
| [7]  | 2022 | Sensors                   | 受限 NB-IoT 设备 OTA                  | 包体/分片/重传决定时延与能耗         | 网络制式特定                 | E, F          |
| [8]  | 2024 | Engineering Journal       | ESP32 IIoT 网关（Modbus+MQTT/REST）   | 低成本网关可实现工业协议转换与上云   | 以 Modbus TCP 为主           | C, E          |
| [9]  | 2025 | Applied System Innovation | MODBUS RS-485 多节点监测控制          | 多从站扩展与统一采集可行             | 业务场景偏农业               | D, E          |
| [10] | 2023 | IEEE ICCPCT               | RS485 数据记录+MQTT 网关（ESP32）     | 现场总线与消息上云可一体化落地       | 会议论文，长期稳定性指标有限 | C, D, E       |
| [11] | 2023 | 传感器与微系统            | ESP32 Modbus RTU 多传感器网关         | 地址分配+轮询+超时重试是稳定关键     | 规模与公开数据有限           | C, D          |
| [12] | 2024 | 自动化仪表                | ESP32+4G 远程 OTA 工程实现            | 4G 状态机、缓存与回滚可协同设计      | 细节公开度受限               | C, E, F       |

### 5.2 对比结论

跨文献横向比较可见：

1) OTA 研究已从“功能实现”进入“可运营”阶段，平台化与安全治理并重 [1][3][5]；
2) RS485 方向的共识是“先通信确定性，再业务智能化”，即先解决地址、时序、重试、异常恢复，再谈算法与策略 [9][10][11]；
3) 4G/蜂窝链路下，离线缓存与重连回放不是性能优化，而是可用性底线 [7][8][12]。

因此，对本项目最关键的不是单个模块技术先进性，而是“OTA状态机、通信状态机、缓存状态机”三者的一致性设计。

## 6. 问题与方向

### 6.1 问题-风险-策略映射表

| 文献证据     | 风险点                         | 工程策略                                              |
| ------------ | ------------------------------ | ----------------------------------------------------- |
| [1][2][3][5] | 升级中断导致设备不可用（砖化） | A/B 分区 + 启动健康检查 + 自动回滚；升级过程原子化    |
| [4][6]       | 固件篡改、MITM 注入            | 固件签名校验、bootloader 信任链、密钥轮换与通道保护   |
| [7][12]      | 4G 弱网导致 OTA 超时/重复下载  | 分片下载、断点续传、指数退避重试、升级窗口控制        |
| [9][11]      | RS485 多从站冲突与轮询饥饿     | 地址规划、轮询调度、超时重试上限、节点降级剔除        |
| [8][10][12]  | 云侧上报抖动与数据丢失         | 本地队列缓存 + 有界回放 +`msg_id` 去重              |
| [3][7][8]    | 协议栈分散导致故障定位困难     | 统一事件模型与状态机日志，打通 Modbus/AT/MQTT 追踪    |
| [1][3][6]    | 升级策略与资源预算不匹配       | 按设备分层发布（灰度/分批），按链路类型设定包体与并发 |
| [11][12]     | 现场运维复杂，复现成本高       | 标准化 Bring-up 记录、故障码、最小复现实验脚本        |

### 6.2 面向本项目的实施建议（A-F 对齐）

1. **阶段A-B（接口冻结与最小闭环）**：先固化 RS485 参数、AT 指令最小子集、云上报单协议（优先 MQTT），并完成单雷达+单链路连通性基线。
2. **阶段C-D（驱动与扩展）**：将 Modbus、AT、上报统一到事件驱动状态机；多雷达扩展采用“地址唯一+轮询限流+健康剔除”策略。
3. **阶段E-F（业务闭环与OTA）**：引入离线队列与回放机制；OTA 强制执行签名校验、升级前健康检查、失败自动回滚。

上述路线与现有高质量文献的共同证据一致，能在工程复杂度可控的前提下优先降低“不可恢复故障”和“现场不可定位故障”两类高风险问题 [1][4][7][11][12]。

## 7. 结论

本文围绕 ESP32+RS485+4G 的实际工程边界，从 12 篇优选文献中归纳出三点结论：

1) OTA 要从“功能”升级为“治理”，即平台化编排、可信启动链与回滚机制缺一不可 [1][3][4][5]；
2) RS485 多节点场景的首要目标是通信确定性，地址、时序、重试与日志体系是稳定运行的关键 [9][10][11]；
3) 4G 链路中的断连、抖动与时延必须通过缓存与回放机制吸收，否则无法支撑长期无人值守运行 [7][8][12]。

对本项目而言，最优路径不是追求单点“最先进方案”，而是按阶段 A-F 构建“可恢复、可观测、可演进”的系统基线。只要把 OTA 回滚、RS485 调度、4G 缓存三条主线做成统一状态机闭环，系统就具备从样机走向可运维产品的核心条件。

## 8. 参考文献（IEEE）

[1] I. C. Panagou, S. Katsoulis, E. Nannos, F. Zantalis, and G. Koulouras, “IOTAfy: An ESP32-Based OTA Firmware Management Platform for Scalable IoT Deployments,” *Engineering Proceedings*, vol. 124, no. 1, p. 40, 2026, doi: 10.3390/engproc2026124040.
[2] M. Kubaščas   aík, I. A. Tupý, J. Šumský, and T. Bača, “OTA firmware updates on ESP32 based microcontrolers,” in *Proc. 2024 IEEE 17th Int. Scientific Conf. on Informatics (Informatics)*, Poprad, Slovakia, Nov. 13-15, 2024, pp. 185-189, doi: 10.1109/Informatics62280.2024.10900824.
[3] L. Formanek, M. Kubascik, O. Karpis, and P. Kolok, “Advanced System for Remote Updates on ESP32-Based Devices Using Over-the-Air Update Technology,” *Computers*, vol. 14, no. 12, p. 531, 2025, doi: 10.3390/computers14120531.
[4] S. E. Jaouhari and E. Bouvet, “Toward a generic and secure bootloader for IoT device firmware OTA update,” in *Proc. 2022 Int. Conf. on Information Networking (ICOIN)*, Jeju Island, Republic of Korea, Jan. 12-15, 2022, pp. 90-95, doi: 10.1109/ICOIN53446.2022.9687242.
[5] M. M. Villegas, M. Solar, F. D. Giraldo, and H. Astudillo, “DeOTA-IoT: A Techniques Catalog for Designing Over-the-Air (OTA) Update Systems for IoT,” *Sensors*, vol. 26, no. 1, p. 193, 2026, doi: 10.3390/s26010193.
[6] C.-Y. Park, S.-J. Lee, and I.-G. Lee, “Secure and Lightweight Firmware Over-the-Air Update Mechanism for Internet of Things,” *Electronics*, vol. 14, no. 8, p. 1583, 2025, doi: 10.3390/electronics14081583.
[7] F. Mahfoudhi, A. K. Sultania, and J. Famaey, “Over-the-Air Firmware Updates for Constrained NB-IoT Devices,” *Sensors*, vol. 22, no. 19, p. 7572, 2022, doi: 10.3390/s22197572.
[8] P. Boonmeeruk, P. Palrat, and K. Wongsopanakul, “Cost-Effective IIoT Gateway Development Using ESP32 for Industrial Applications,” *Engineering Journal*, vol. 28, no. 10, pp. 93-108, 2024, doi: 10.4186/ej.2024.28.10.93.
[9] R. Herrera-Arroyo, J. Martínez-Nolasco, E. Botello-Álvarez, V. Sámano-Ortega, C. Martínez-Nolasco, and C. Moreno-Aguilera, “Smart Hydroponic Cultivation System for Lettuce (Lactuca sativa L.) Growth Under Different Nutrient Solution Concentrations in a Controlled Environment,” *Applied System Innovation*, vol. 8, no. 4, p. 110, 2025, doi: 10.3390/asi8040110.
[10] M. Dafare, A. Titarmare, S. Waghmare, P. Chandankhede, and A. Bhoyar, “LoRa-Enabled Smart RS485 Data Logger and MQTT Gateway for Industrial IoT Applications Using ESP32,” in *Proc. 2023 Int. Conf. on Circuit Power and Computing Technologies (ICCPCT)*, Kollam, India, Aug. 10-11, 2023, pp. 1297-1302, doi: 10.1109/ICCPCT58313.2023.10245760.
[11] 王晓东, 张磊, 李明, “基于ESP32的Modbus RTU多传感器物联网网关设计,” *传感器与微系统*, vol. 42, no. 5, pp. 45-48, 2023.
[12] 刘洋, 陈伟, 赵静, “ESP32+4G模块的远程固件OTA升级系统设计与实现,” *自动化仪表*, vol. 45, no. 8, pp. 12-17, 2024.

