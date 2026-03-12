from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import psutil
import socket
import time
import os
import sys
import platform
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

app = FastAPI()
boot_time = psutil.boot_time()
psutil.cpu_percent(interval=None)   # prime baseline
DB_PATH = Path(__file__).resolve().parent / "metrics.db"
SAMPLE_INTERVAL_SEC = 5
RETENTION_DAYS = 7


def get_cpu_temp_c() -> Optional[float]:
    """
    Read CPU temp from Linux thermal interface.
    Returns Celsius as float (rounded to 1 decimal) or None if unavailable.
    """
    path = "/sys/class/thermal/thermal_zone0/temp"
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()

        val = float(raw)
        # Usually millidegrees C on Pi (e.g., 51234)
        if val > 1000:
            val /= 1000.0

        # sanity bounds
        if val < -20 or val > 120:
            return None

        return round(val, 1)
    except (FileNotFoundError, ValueError, OSError):
        return None


def get_lan_ip() -> Optional[str]:
    """
    Best-effort LAN IP (avoids 127.*).
    Returns None if unavailable.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # no packets sent; used to determine default route IP
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return None

def iso_utc(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def get_os_pretty_name() -> Optional[str]:
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        return None
    return None


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            ts_utc INTEGER PRIMARY KEY,
            cpu_percent REAL,
            cpu_temp_c REAL,
            memory_percent REAL,
            disk_percent REAL,
            load_1 REAL,
            load_5 REAL,
            load_15 REAL
        )
        """)
        con.commit()
    finally:
        con.close()



def write_sample(row: dict) -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("""
        INSERT OR REPLACE INTO samples
        (ts_utc, cpu_percent, cpu_temp_c, memory_percent, disk_percent, load_1, load_5, load_15)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["ts_utc"],
            row["cpu_percent"],
            row["cpu_temp_c"],
            row["memory_percent"],
            row["disk_percent"],
            row["load_1"],
            row["load_5"],
            row["load_15"],
        ))
        con.commit()
    finally:
        con.close()


def prune_old_samples() -> None:
    cutoff = int(time.time()) - (RETENTION_DAYS * 24 * 3600)
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("DELETE FROM samples WHERE ts_utc < ?", (cutoff,))
        con.commit()
    finally:
        con.close()


def read_history(minutes: int = 60, limit: int = 5000, step: int = 1) -> list[dict]:
    minutes = max(1, min(int(minutes), 24 * 60))  # clamp 1..1440
    since_ts = int(time.time()) - (minutes * 60)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT ts_utc, cpu_percent, cpu_temp_c, memory_percent, disk_percent, load_1, load_5, load_15
            FROM samples
            WHERE ts_utc >= ?
            ORDER BY ts_utc ASC
            LIMIT ?
        """, (since_ts, limit)).fetchall()

        step = max(1, int(step))
        rows = rows[::step]

        return [dict(r) for r in rows]
    finally:
        con.close()



def collect_sample() -> dict:
    # Keep it lightweight and non-blocking
    disk = psutil.disk_usage("/")
    vm = psutil.virtual_memory()

    try:
        load_1, load_5, load_15 = os.getloadavg()
    except Exception:
        load_1 = load_5 = load_15 = None

    return {
        "ts_utc": int(time.time()),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_temp_c": get_cpu_temp_c(),
        "memory_percent": vm.percent,
        "disk_percent": disk.percent,
        "load_1": load_1,
        "load_5": load_5,
        "load_15": load_15,
    }


def log_worker() -> None:
    n = 0
    while True:
        try:
            row = collect_sample()
            write_sample(row)

            # prune about once per hour
            n += 1
            if n % (3600 // SAMPLE_INTERVAL_SEC) == 0:
                prune_old_samples()

        except Exception:
            pass

        time.sleep(SAMPLE_INTERVAL_SEC)



@app.get("/", response_class=HTMLResponse)
def root():
    return dashboard()


@app.get("/health")
def health():
    # Simple “is the service alive” endpoint
    return {"status": "ok"}



@app.on_event("startup")
def _startup():
    init_db()
    t = threading.Thread(target=log_worker, daemon=True)
    t.start()

@app.get("/metrics/history")
def metrics_history(minutes: int = 60, step: int = 1):
    return {
        "minutes": minutes,
        "step": step,
        "points": read_history(minutes=minutes, step=step),
    }


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
    <html>
      <head>
        <title>Tart-Pi Dashboard</title>
        <style>
          body {
            font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
            margin: 24px;
            background: #fafafa;
            color: #111;
          }
          h1 {
            margin: 0 0 16px 0;
            font-size: 28px;
          }
          #cards {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  align-items: stretch;
}

#cards > div {
  box-sizing: border-box;
}

  </style>
</head>

<body>
        <h1>Tart-Pi Engineering Dashboard</h1>

<div id="cards"></div>

<script>
function card(title, value, tone){
  const fg =
    tone === "good" ? "#2e7d32" :
    tone === "warn" ? "#f9a825" :
    tone === "bad"  ? "#c62828" :
    "#111";

  const bg =
    tone === "good" ? "#e8f5e9" :
    tone === "warn" ? "#fff8e1" :
    tone === "bad"  ? "#ffebee" :
    "#ffffff";

  const border =
    tone === "good" ? "#a5d6a7" :
    tone === "warn" ? "#ffe082" :
    tone === "bad"  ? "#ef9a9a" :
    "#ddd";

  return `
      <div
         style="border:1px solid ${border}; background:${bg}; border-radius:10px; padding:14px; flex: 1 1 260px; max-width: 320px; transition:transform 120ms ease, box-shadow 120ms ease;"  
         onmouseenter="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 8px 18px rgba(0,0,0,0.08)';"
         onmouseleave="this.style.transform='translateY(0px)'; this.style.boxShadow='none';"
      >
      <div style="font-size:14px; color:#555;">${title}</div>
      <div style="
  font-size:28px;
  font-weight:700;
  margin-top:6px;
  color:${fg};
  word-break: break-word;
  overflow-wrap: anywhere;
  line-height: 1.15;
">${value}</div>

    </div>
  `;
}


function fmtOrNA(v){
  return (v === null || v === undefined) ? "N/A" : v;
}

function tonePct(x, goodMax, warnMax){
  if (x === null || x === undefined) return null;
  if (x < goodMax) return "good";
  if (x < warnMax) return "warn";
  return "bad";
}

function toneTempC(t){
  if (t === null || t === undefined) return null;
  if (t < 60) return "good";
  if (t < 75) return "warn";
  return "bad";
}

async function refresh(){
  const r = await fetch("/metrics");
  const j = await r.json();
  const cpuTone = tonePct(j.cpu_percent, 50, 70);
  const memTone = tonePct(j.memory_percent, 60, 80);
  const diskTone = tonePct(j.disk_percent, 70, 85);
  const tempTone = toneTempC(j.cpu_temp_c);


  const cpuTemp = (j.cpu_temp_c !== null && j.cpu_temp_c !== undefined)
    ? `${j.cpu_temp_c.toFixed(1)} °C`
    : "N/A";

  const loadAvg = (j.load_avg_1 !== null && j.load_avg_5 !== null && j.load_avg_15 !== null)
    ? `${j.load_avg_1.toFixed(2)} / ${j.load_avg_5.toFixed(2)} / ${j.load_avg_15.toFixed(2)}`
    : "N/A";

const cards = [
  // Identity
  card("Hostname", fmtOrNA(j.hostname)),
  card("IP Address", fmtOrNA(j.ip_address)),
  card("OS", fmtOrNA(j.os_pretty_name)),
  card("Python", fmtOrNA(j.python_version)),
  card("Processes", fmtOrNA(j.process_count)),
  card("Cores (physical)", fmtOrNA(j.cpu_cores_physical)),


  // CPU
  card("CPU %", (j.cpu_percent ?? 0).toFixed(1), cpuTone),
  card("CPU Temp", cpuTemp, tempTone),
  card("Cores (logical)", fmtOrNA(j.cpu_cores_logical)),
  card("Load Avg (1/5/15)", loadAvg),

  // Memory
  card("Memory %", (j.memory_percent ?? 0).toFixed(1), memTone),
  card("RAM Total (GB)", (j.ram_total_gb ?? 0).toFixed(2)),

  // Disk
  card("Disk %", (j.disk_percent ?? 0).toFixed(1), diskTone),
  card("Disk Free (GB)", (j.disk_free_gb ?? 0).toFixed(2)),
  card("Disk Total (GB)", (j.disk_total_gb ?? 0).toFixed(2)),

  // Uptime
  card("Uptime (hrs)", (j.uptime_hours ?? 0).toFixed(2)),
  card("Uptime (sec)", fmtOrNA(j.uptime_seconds)),
  card("Boot (UTC)", fmtOrNA(j.boot_timestamp_utc)),
];

  document.getElementById("cards").innerHTML = cards.join("");
}

refresh();
setInterval(refresh, 2000);
</script>

      </body>
    </html>
    """


@app.get("/metrics")
def metrics():
    now = time.time()

    uptime_seconds = int(now - boot_time)
    uptime_hours = round(uptime_seconds / 3600, 2)
    boot_timestamp_utc = iso_utc(boot_time)

    disk = psutil.disk_usage("/")
    free_gb = round(disk.free / (1024**3), 2)
    total_gb = round(disk.total / (1024**3), 2)

    hostname = socket.gethostname()
    ip_address = get_lan_ip()

    vm = psutil.virtual_memory()
    ram_total_gb = round(vm.total / (1024**3), 2)

    # Linux load averages; may fail on non-POSIX systems (but fine on Debian)
    try:
        load_1, load_5, load_15 = os.getloadavg()
    except Exception:
        load_1 = load_5 = load_15 = None

    # IMPORTANT: non-blocking cpu_percent (no interval=1)
    # First call may be 0.0 if never "primed", but after refreshes it stabilizes.
    cpu_percent = psutil.cpu_percent(interval=None)

    return {
        "hostname": hostname,
        "ip_address": ip_address,              # None => JSON null (more consistent than "Unavailable")

        "cpu_percent": cpu_percent,
        "cpu_temp_c": get_cpu_temp_c(),

        "load_avg_1": load_1,
        "load_avg_5": load_5,
        "load_avg_15": load_15,
        "cpu_cores_logical": psutil.cpu_count(logical=True),

        "memory_percent": vm.percent,
        "ram_total_gb": ram_total_gb,

        "disk_percent": disk.percent,
        "disk_free_gb": free_gb,
        "disk_total_gb": total_gb,

        "uptime_hours": uptime_hours,
        "uptime_seconds": uptime_seconds,
        "boot_timestamp_utc": boot_timestamp_utc,

        "os_pretty_name": get_os_pretty_name(),
        "python_version": platform.python_version(),
        "process_count": len(psutil.pids()),
        "cpu_cores_physical": psutil.cpu_count(logical=False),

    }
