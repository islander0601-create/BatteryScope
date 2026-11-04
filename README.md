# BatteryScope — 充放电功率实时监控

仿 HWiNFO64 的电池/功率监视面板，纯本地采集，数据不上传任何服务器。

## 快速开始

**方式一：双击 `启动.bat`**（推荐）
自动定位带依赖的 Python、启动服务并打开浏览器。

**方式二：命令行**

```bash
python battery_server.py                    # 默认 http://127.0.0.1:8765
python battery_server.py --port 9000        # 换端口
python battery_server.py --interval 0.25    # 250ms 采样，更平滑
python battery_server.py --history 7200     # 2 小时缓冲
python battery_server.py --no-browser       # 不自动开浏览器
```

## 依赖

```
fastapi
uvicorn
websockets
```

安装：
```bash
pip install fastapi uvicorn websockets
```

## 功能

| 区域 | 内容 |
|---|---|
| 顶部状态 | 连接状态、实际采样率、缓冲条数、暂停/导出/清空 |
| 仪表卡 | 充电功率、放电功率、电压、电流、电量、本次充电会话，各带迷你曲线与统计 |
| 主图表 | 5 条曲线可任意勾选/隐藏，悬停显示十字线与逐点数值 |
| 采样明细 | 最近 80 行倒序表格，含温度、循环次数、充放电状态标签 |

### 充电会话统计

「平均充电功率」按**本次插上插头**计算，而非整个缓冲区：

- 从 `charging` 状态由 `false → true` 的翻转点开始，到当前为止
- 断开电源后会话结束，下次插入重新计数
- 该卡片显示：平均功率、峰值、持续时长、累计送入电量（Wh）

这样避免了全程平均被大量空闲 0W 样本稀释——插上电瞬间的真实充电功率才是有意义的指标。

### 续航预估

当处于电池供电状态时，按**近期 30 秒平均放电功率**估算剩余可用时间：

```
可用小时 = RemainingCapacity(mWh) / 平均放电功率(mW)
```

插电时不显示预估；近期无放电样本（如刚插上电）时同样不显示，避免除零得出荒谬数值。

指标定义：
- **充电功率** — `BatteryStatus.ChargeRate` (mW) ÷ 1000，即流入电池的功率
- **放电功率** — `BatteryStatus.DischargeRate` (mW) ÷ 1000
- **电压/电流** — `BatteryStatus.Voltage` (mV) ÷ 1000；电流 = 功率 ÷ 电压
- **状态** — `Win32_Battery.BatteryStatus`（2=已接交流电，3=放电中，6=充电中…）

## 接口

| 路径 | 说明 |
|---|---|
| `GET /` | 主界面 |
| `GET /api/snapshot` | 当前值 + 全部历史 + 统计 |
| `GET /api/export.csv` | 导出 CSV |
| `WS /ws` | 实时推流（先发 history，再逐条发 sample） |

## 架构

```
battery_server.py     FastAPI 服务 + WebSocket 广播 + 环形缓冲 + CSV 导出
collector.ps1         常驻 PowerShell 采集器，逐行输出 JSONL（ASCII 编码）
static/index.html     前端界面，Canvas 手绘图表，零外部依赖
启动.bat              双击启动器，自动探测 Python 环境
```

**为什么用常驻 PowerShell 而不是每次采样起一个进程？**
`Get-CimInstance` 单次调用约 80–150ms，若每次采样都重启 PowerShell 进程，开销会吃掉一半采样间隔。常驻进程以 0.5s 为周期持续输出，Python 端只做行读取 + JSON 解析，负载极低。

**编码注意：** PowerShell 5.1 按 GBK 读取无 BOM 的 UTF-8 脚本会乱码，因此 `collector.ps1` 存为带 BOM 的 UTF-8；同时采集器只输出 ASCII 状态码，中文文案统一在 Python 端映射，彻底避免管道编码问题。

## 已知限制

- 电池温度 `tempC` 取决于固件是否上报 `BatteryTemperature` 类，部分机型为空
- `fullCap` 同理，取决于 `Win32_Battery.FullChargeCapacity` 是否填充
- 显示的是**电池侧**功率，与适配器铭牌标称值不同（适配器标称 65W 属上限，涓流阶段实际只有十几瓦）
