"""
BatteryScope —— ThinkPad 充放电功率实时监控

后端：FastAPI + WebSocket 推送，前端 Canvas 绘图（零前端依赖）
数据源：常驻 PowerShell 采集器，逐行输出 JSONL，避免每次采样重启进程的开销

启动：
    python battery_server.py                # 默认 http://127.0.0.1:8765
    python battery_server.py --port 9000
    python battery_server.py --interval 0.25 --history 3600
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from collections import deque
from datetime import datetime

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COLLECTOR = os.path.join(BASE_DIR, "collector.ps1")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# 电源状态码 → 中文文案（采集器只输出 ASCII，文案统一在此映射）
AC_STATE_TEXT = {
    "ON_BATTERY": "电池供电",
    "AC_CONNECTED": "已接交流电",
    "DISCHARGING": "放电中",
    "LOW": "低电量",
    "SAVER": "省电模式",
    "CHARGING": "充电中",
    "ON_BATTERY_CHARGING": "电池供电+充电",
    "CHARGING_REDUCED": "充电中(降级)",
    "ON_BATTERY_HIGH": "电池供电(高)",
    "PARTIAL_CHARGE": "部分充电",
    "FULLY_CHARGED": "完全充满",
}

# ---------------------------------------------------------------- 全局状态

class Telemetry:
    """采集器进程 + 环形缓冲 + 订阅者队列"""

    def __init__(self, interval: float, history: int):
        self.interval = interval
        self.history = history
        self.samples: deque = deque(maxlen=history)
        self.subscribers: set = set()
        self.latest: dict | None = None
        self.proc: subprocess.Popen | None = None
        self._lock = asyncio.Lock()
        self.started_at = time.time()
        self.error: str | None = None

    # --- 生命周期 ---
    def start(self):
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", COLLECTOR],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )
        # 把采样间隔喂给采集器
        self.proc.stdin.write(f"{self.interval}\n")
        self.proc.stdin.flush()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            self.proc.terminate()

    # --- 数据泵 ---
    async def pump(self):
        """后台任务：读取采集器输出，解析并广播"""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def reader():
            try:
                for raw in self.proc.stdout:
                    queue.put_nowait(raw)
            except Exception as exc:  # pragma: no cover
                self.error = str(exc)

        # reader 跑在独立线程，避免阻塞事件循环
        import threading
        threading.Thread(target=reader, daemon=True).start()

        while True:
            raw = await queue.get()
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "error" in data:
                self.error = data["error"]
                continue

            data["t"] = datetime.now().strftime("%H:%M:%S")
            data["acText"] = AC_STATE_TEXT.get(data.get("acCode", ""), "未知")
            self.latest = data
            self.samples.append(data)

            # 广播
            dead = []
            for q in list(self.subscribers):
                try:
                    q.put_nowait(data)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self.subscribers.discard(q)

    # --- 统计 ---
    def stats(self) -> dict:
        if not self.samples:
            return {}
        chg = [s["chargeW"] for s in self.samples if s.get("chargeW")]
        dis = [s["dischargeW"] for s in self.samples if s.get("dischargeW")]
        vol = [s["voltV"] for s in self.samples if s.get("voltV")]
        amps = [s["amp"] for s in self.samples if s.get("amp")]

        def agg(arr):
            if not arr:
                return {"min": 0, "max": 0, "avg": 0}
            return {
                "min": round(min(arr), 2),
                "max": round(max(arr), 2),
                "avg": round(sum(arr) / len(arr), 2),
            }

        return {
            "charge": agg(chg),
            "discharge": agg(dis),
            "voltage": agg(vol),
            "amp": agg(amps),
            "count": len(self.samples),
        }


telemetry: Telemetry = None  # 由 main 注入
app = FastAPI(title="BatteryScope", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------- 路由

@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/snapshot")
async def snapshot():
    """当前状态 + 历史 + 统计"""
    return JSONResponse({
        "latest": telemetry.latest,
        "history": list(telemetry.samples),
        "stats": telemetry.stats(),
        "uptime": round(time.time() - telemetry.started_at, 1),
        "interval": telemetry.interval,
        "error": telemetry.error,
    })


@app.get("/api/export.csv")
async def export_csv():
    """导出全部采样为 CSV"""
    if not telemetry.samples:
        return Response("no data", status_code=204)

    head = ["timestamp", "time", "charge_W", "discharge_W", "net_W", "voltage_V",
            "current_A", "AC_state", "level_pct", "temp_C", "cycle_count"]
    lines = [",".join(head)]
    for s in telemetry.samples:
        lines.append(",".join([
            str(s.get("ts", "")), s.get("t", ""),
            f'{s.get("chargeW", 0):.3f}', f'{s.get("dischargeW", 0):.3f}',
            f'{s.get("netW", 0):.3f}', f'{s.get("voltV", 0):.3f}',
            f'{s.get("amp", 0):.3f}', s.get("acCode", ""),
            str(s.get("levelPct", "")), str(s.get("tempC", "")), str(s.get("cycle", "")),
        ]))
    body = "\n".join(lines)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        body, media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="battery_{stamp}.csv"',
            "Content-Type": "text/csv; charset=utf-8",
        },
    )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    telemetry.subscribers.add(q)
    try:
        # 先推历史，让图表立刻有内容
        if telemetry.samples:
            await ws.send_json({
                "type": "history",
                "data": list(telemetry.samples),
                "stats": telemetry.stats(),
            })
        while True:
            data = await q.get()
            await ws.send_json({"type": "sample", "data": data})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        telemetry.subscribers.discard(q)


# ---------------------------------------------------------------- 启动

def main():
    global telemetry

    ap = argparse.ArgumentParser(description="BatteryScope 充放电功率实时监控")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--interval", type=float, default=0.5, help="采样间隔(秒)")
    ap.add_argument("--history", type=int, default=3600, help="历史缓冲条数")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    telemetry = Telemetry(interval=args.interval, history=args.history)
    telemetry.start()

    @app.on_event("startup")
    async def _startup():
        asyncio.create_task(telemetry.pump())

    @app.on_event("shutdown")
    async def _shutdown():
        telemetry.stop()

    url = f"http://{args.host}:{args.port}/"
    print(f"\n  BatteryScope 已启动")
    print(f"  采集间隔 {args.interval}s   缓冲 {args.history} 条")
    print(f"  访问地址 {url}\n")

    if not args.no_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
