#!/usr/bin/env python3
"""
Monitoring Exporter (UDP)

Este script se ejecuta en cada servidor Linux monitorizado (agent/exporter) y se encarga de:

1) Recolectar métricas del HOST (CPU, RAM, SWAP, discos, red opcional, uptime...).
2) Recolectar métricas de SERVICIOS (Apache2, MariaDB/MySQL, SSH...).
3) Construir un mensaje JSON normalizado con toda la información.
4) Enviar el JSON al Recolector Central (RC) mediante UDP.

Diseño:
- UDP se usa para reducir overhead y simplificar el envío (no hay handshake).
- El RC guarda el documento en MongoDB.
- El exporter NUNCA debe romperse por fallos de servicios: si un servicio falla,
  se devuelve un objeto con "enabled": false y un campo "error".

IMPORTANTE:
- Ya NO se usa token de autenticación.
- server_id es únicamente un identificador para distinguir hosts en MongoDB.

Requisitos:
- Python 3
- psutil
- plugins en carpeta services/ (apache2.py, mariadb.py, ssh.py, etc.)
"""

import argparse
import json
import logging
import os
import platform
import socket
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import psutil

# Servicios (plugins)
from services.apache2 import fetch_apache_status
from services.mariadb import fetch_mariadb_status
from services.ssh import fetch_ssh_status


def setup_logging(debug: bool) -> None:
    """
    Configura el sistema de logs del exporter.

    Los logs van a stdout/stderr y systemd/journald los recoge automáticamente.

    Formato:
        2026-01-19 18:31:47,710 [INFO] exporter: Mensaje

    Args:
        debug (bool):
            Si True, el nivel será DEBUG.
            Si False, el nivel será INFO.
    """
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] exporter: %(message)s",
    )


def read_server_id(path: str) -> str:
    """
    Lee el server_id desde un fichero local.

    El fichero debe contener el ID en una sola línea.
    Ejemplo:
        deb13

    Args:
        path (str):
            Ruta del fichero (ej: /etc/monitoring/server_id)

    Returns:
        str:
            server_id leído del fichero.

    Raises:
        RuntimeError:
            Si el fichero no existe, no se puede leer o está vacío.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            sid = f.read().strip()
    except FileNotFoundError:
        raise RuntimeError(f"server_id file not found: {path}")
    except Exception as e:
        raise RuntimeError(f"cannot read server_id file {path}: {e}")

    if not sid:
        raise RuntimeError(f"server_id file is empty: {path}")

    return sid


def iso_utc_now() -> str:
    """
    Devuelve el timestamp actual en UTC en formato ISO8601.

    Ejemplo:
        2026-01-19T18:31:47.710Z

    Returns:
        str: Fecha/hora actual en UTC en formato ISO con sufijo 'Z'.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def safe_loadavg() -> List[float]:
    """
    Devuelve la carga promedio del sistema (load average) en 1, 5 y 15 minutos.

    Returns:
        List[float]:
            [load1, load5, load15] redondeado a 2 decimales o [] si falla.
    """
    try:
        la = os.getloadavg()
        return [round(float(x), 2) for x in la]
    except Exception:
        return []


def collect_disks() -> List[Dict[str, Any]]:
    """
    Recolecta el uso de disco para particiones "reales".

    Returns:
        List[Dict[str, Any]]: lista de discos/particiones detectadas.
    """
    ignore_fstypes = {
        "tmpfs", "devtmpfs", "proc", "sysfs", "overlay", "squashfs",
        "cgroup", "cgroup2", "pstore", "autofs", "debugfs", "tracefs",
        "securityfs", "fusectl", "mqueue", "hugetlbfs", "ramfs"
    }

    disks: List[Dict[str, Any]] = []

    for p in psutil.disk_partitions(all=False):
        if not p.mountpoint or p.fstype in ignore_fstypes:
            continue

        try:
            usage = psutil.disk_usage(p.mountpoint)
        except PermissionError:
            continue

        disks.append({
            "mount": p.mountpoint,
            "fstype": p.fstype,
            "device": p.device,
            "used_bytes": usage.used,
            "total_bytes": usage.total,
            "percent": round(usage.percent, 2),
        })

    return disks


def collect_metrics():
    """
    Recolecta métricas del host.

    Returns:
        Tuple[Dict[str, Any], int]:
            metrics: dict con métricas del host
            uptime_s: uptime en segundos
    """
    cpu_percent = psutil.cpu_percent(interval=None)
    cores = psutil.cpu_count(logical=True) or 0

    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    swap_present = sm.total > 0

    metrics = {
        "cpu": {
            "percent": round(cpu_percent, 2),
            "cores": cores,
            "loadavg": safe_loadavg(),
        },
        "mem": {
            "used_bytes": vm.used,
            "total_bytes": vm.total,
            "percent": round(vm.percent, 2),
        },
        "swap": {
            "present": swap_present,
            "used_bytes": sm.used if swap_present else 0,
            "total_bytes": sm.total if swap_present else 0,
            "percent": round(sm.percent, 2) if swap_present else 0.0,
        },
        "disks": collect_disks(),
    }

    nio = psutil.net_io_counters(pernic=False)
    metrics["net"] = {
        "rx_bytes_total": nio.bytes_recv,
        "tx_bytes_total": nio.bytes_sent,
    }

    uptime_s = int(time.time() - psutil.boot_time())
    return metrics, uptime_s


def collect_services(
    enabled_services: List[str],
    apache_status_url: str,
    mariadb_host: str,
    mariadb_port: int,
    mariadb_user: str,
    mariadb_password: str,
    ssh_host: str,
    ssh_port: int,
) -> Dict[str, Any]:
    """
    Recolecta métricas de servicios habilitados.

    Args:
        enabled_services (List[str]):
            Lista de servicios habilitados por CLI.

    Returns:
        Dict[str, Any]:
            Diccionario con claves por servicio.
    """
    services: Dict[str, Any] = {}

    if not enabled_services:
        return services

    for svc in enabled_services:
        svc = svc.strip().lower()

        if svc == "apache2":
            logging.debug("Collecting apache2 metrics from %s", apache_status_url)
            services["apache2"] = fetch_apache_status(url=apache_status_url, timeout=1.5)

        elif svc in ("mariadb", "mysql"):
            logging.debug(
                "Collecting mariadb/mysql metrics from %s:%d user=%s",
                mariadb_host, mariadb_port, mariadb_user
            )
            services["mariadb"] = fetch_mariadb_status(
                host=mariadb_host,
                port=mariadb_port,
                user=mariadb_user,
                password=mariadb_password,
                timeout=1.5,
            )

        elif svc in ("ssh", "sshd"):
            logging.debug("Collecting ssh metrics host=%s port=%d", ssh_host, ssh_port)
            services["ssh"] = fetch_ssh_status(host=ssh_host, port=ssh_port)

        else:
            services[svc] = {
                "enabled": False,
                "error": "unsupported_service"
            }

    return services


def build_message(
    server_id: str,
    enabled_services: List[str],
    apache_status_url: str,
    mariadb_host: str,
    mariadb_port: int,
    mariadb_user: str,
    mariadb_password: str,
    ssh_host: str,
    ssh_port: int,
) -> Dict[str, Any]:

    metrics, uptime_s = collect_metrics()

    services_data = collect_services(
        enabled_services=enabled_services,
        apache_status_url=apache_status_url,
        mariadb_host=mariadb_host,
        mariadb_port=mariadb_port,
        mariadb_user=mariadb_user,
        mariadb_password=mariadb_password,
        ssh_host=ssh_host,
        ssh_port=ssh_port,
    )

    return {
        "server_id": server_id,
        "ts": iso_utc_now(),
        "host": {
            "uptime_s": uptime_s
        },
        "metrics": metrics,
        "services": services_data,
    }


def main() -> int:
    """
    Punto de entrada del exporter.
    """
    parser = argparse.ArgumentParser(description="Monitoring Exporter (UDP)")

    parser.add_argument("--rc-host", required=True, help="IP/hostname del Recolector Central (RC)")
    parser.add_argument("--rc-port", type=int, default=9000, help="Puerto UDP del RC (default: 9000)")

    parser.add_argument(
        "--server-id-file",
        default="/etc/monitoring/server_id",
        help="Ruta del fichero que contiene el server_id (default: /etc/monitoring/server_id)",
    )

    parser.add_argument("--interval", type=int, default=10, help="Intervalo de envío en segundos (default: 10)")
    parser.add_argument("--debug", action="store_true", help="Activa logs en modo DEBUG")

    parser.add_argument(
        "--services",
        nargs="*",
        default=[],
        help="Lista de servicios a monitorizar (ej: apache2 mariadb mysql ssh)",
    )

    parser.add_argument(
        "--apache-status-url",
        default="http://127.0.0.1/server-status?auto",
        help="URL del mod_status de Apache2",
    )

    parser.add_argument("--mariadb-host", default="127.0.0.1")
    parser.add_argument("--mariadb-port", type=int, default=3306)
    parser.add_argument("--mariadb-user", default="monitoring")
    parser.add_argument("--mariadb-password", default="")

    parser.add_argument("--ssh-host", default="127.0.0.1")
    parser.add_argument("--ssh-port", type=int, default=22)

    args = parser.parse_args()

    setup_logging(args.debug)

    # Leemos server_id desde fichero
    try:
        server_id = read_server_id(args.server_id_file)
    except Exception:
        logging.exception("Cannot load server_id")
        return 1

    enabled_services = [s.strip().lower() for s in args.services if s.strip()]

    logging.info("Exporter starting (server_id=%s)", server_id)
    logging.info(
        "RC=%s:%d interval=%ds services=%s",
        args.rc_host,
        args.rc_port,
        args.interval,
        enabled_services if enabled_services else "none",
    )

    # PRIMING CPU
    psutil.cpu_percent(interval=None)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    while True:
        try:
            msg = build_message(
                server_id=server_id,
                enabled_services=enabled_services,
                apache_status_url=args.apache_status_url,
                mariadb_host=args.mariadb_host,
                mariadb_port=args.mariadb_port,
                mariadb_user=args.mariadb_user,
                mariadb_password=args.mariadb_password,
                ssh_host=args.ssh_host,
                ssh_port=args.ssh_port,
            )

            payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")

            logging.debug("Payload size=%d bytes", len(payload))
            if len(payload) > 1400:
                logging.warning("Payload near MTU limit (%d bytes)", len(payload))

            sock.sendto(payload, (args.rc_host, args.rc_port))
            logging.info("Metrics sent")

        except Exception:
            logging.exception("Exporter cycle failed")

        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
