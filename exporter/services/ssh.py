# services/ssh.py
import socket
import subprocess
from typing import Dict, Any


def _systemctl_is_active(service_name: str, timeout: float = 1.0) -> str:
    """
    Devuelve: active / inactive / failed / unknown
    """
    try:
        res = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (res.stdout or "").strip()
        if out:
            return out
        return "unknown"
    except Exception:
        return "unknown"


def _tcp_port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    """
    Comprueba si un puerto TCP está abierto en host:port.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _count_ssh_sessions(timeout: float = 1.5) -> int:
    """
    Cuenta sesiones ssh activas aproximadas usando 'who'.
    """
    try:
        res = subprocess.run(
            ["who"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        lines = [l for l in (res.stdout or "").splitlines() if l.strip()]
        pts = [l for l in lines if "pts/" in l]
        return len(pts)
    except Exception:
        return -1


def fetch_ssh_status(
    port: int = 22,
    host: str = "127.0.0.1",
) -> Dict[str, Any]:
    """
    Monitorización simple de SSH/sshd (RECORTADO).

    Recorte aplicado:
    - Eliminamos "service_name" y el bloque "listen.host" (no aporta mucho)
    - Dejamos:
        enabled, systemd_state, listen.port, listen.port_open, sessions_estimated
    """

    ssh_state = _systemctl_is_active("ssh")
    sshd_state = _systemctl_is_active("sshd")

    # elegimos el que esté mejor
    service_state = ssh_state
    if ssh_state == "unknown" and sshd_state != "unknown":
        service_state = sshd_state
    elif ssh_state != "active" and sshd_state == "active":
        service_state = sshd_state

    port_open = _tcp_port_open(host, port)
    sessions = _count_ssh_sessions()

    enabled = (service_state == "active") or port_open

    return {
        "enabled": bool(enabled),
        "systemd_state": service_state,
        "listen": {
            "port": int(port),
            "port_open": bool(port_open),
        },
        "sessions_estimated": int(sessions),
    }
