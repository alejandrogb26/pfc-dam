# services/apache2.py
import urllib.request
from typing import Dict, Any


def _to_number(val: str):
    """
    Convierte una cadena de texto a número (int o float) si es posible.
    Si no, devuelve el string original.
    """
    v = val.strip()
    if v == "":
        return None

    try:
        if v.isdigit() or (v.startswith("-") and v[1:].isdigit()):
            return int(v)
    except Exception:
        pass

    try:
        return float(v)
    except Exception:
        return v


def _parse_scoreboard(sb: str) -> Dict[str, int]:
    """
    Cuenta estados del scoreboard de Apache y devuelve un dict {estado: count}.
    """
    counts: Dict[str, int] = {}
    for ch in sb.strip():
        counts[ch] = counts.get(ch, 0) + 1
    return counts


def fetch_apache_status(
    url: str = "http://127.0.0.1/server-status?auto",
    timeout: float = 1.5
) -> Dict[str, Any]:
    """
    Lee métricas de Apache2 desde mod_status (?auto) y devuelve JSON normalizado RECORTADO.

    Recorte aplicado:
    - Eliminamos campos informativos/ruido:
        status_url, server_version, built, restart_time, current_time, client_ip, mpm...
    - Eliminamos scoreboard.raw (cadena enorme) y nos quedamos solo con counts.
    """

    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            text = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return {
            "enabled": False,
            "error": f"cannot_fetch_status: {e}",
        }

    raw: Dict[str, Any] = {}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # La primera línea a veces es una IP suelta, la ignoramos
        if ":" not in line:
            continue

        k, v = line.split(":", 1)
        raw[k.strip()] = _to_number(v.strip())

    scoreboard = raw.get("Scoreboard")
    scoreboard_counts = _parse_scoreboard(scoreboard) if isinstance(scoreboard, str) else {}

    # JSON final RECORTADO
    return {
        "enabled": True,

        # uptime del servicio Apache (no del host)
        "uptime_s": raw.get("ServerUptimeSeconds") or raw.get("Uptime"),

        # rendimiento
        "req_per_sec": raw.get("ReqPerSec"),
        "bytes_per_sec": raw.get("BytesPerSec"),
        "bytes_per_req": raw.get("BytesPerReq"),

        # workers
        "workers": {
            "busy": raw.get("BusyWorkers"),
            "idle": raw.get("IdleWorkers"),
        },

        # conexiones (event MPM)
        "connections": {
            "total": raw.get("ConnsTotal"),
            "async_wait_io": raw.get("ConnsAsyncWaitIO"),
            "async_writing": raw.get("ConnsAsyncWriting"),
            "async_keepalive": raw.get("ConnsAsyncKeepAlive"),
            "async_closing": raw.get("ConnsAsyncClosing"),
        },

        # scoreboard reducido (solo counts)
        "scoreboard": {
            "counts": scoreboard_counts,
        },
    }
