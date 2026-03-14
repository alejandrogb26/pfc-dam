#!/usr/bin/env python3
"""
Recolector Central (RC) - UDP -> MongoDB

Este programa se ejecuta en la máquina central y se encarga de:

1) Escuchar datagramas UDP en un puerto (por defecto 9000/udp).
2) Recibir JSON enviados por exporters instalados en servidores Linux.
3) Validar:
   - estructura mínima del payload
   - server_id (identificador del servidor)
   - timestamp (ts)
4) Normalizar el documento para MongoDB:
   - convertir timestamp ISO8601 a objeto datetime
   - añadir source_ip del exporter
   - guardar metrics + host + services
5) Insertar el documento en MongoDB (colección definida en config).

IMPORTANTE:
- Ya NO se utiliza autenticación por token.
- El server_id se usa únicamente para identificar el servidor en la base de datos.
- Para evitar inserciones maliciosas:
  - recomienda filtrar por firewall (iptables/ufw) quién puede enviar UDP al puerto del RC.

Características:
- Basado en asyncio.DatagramProtocol (asíncrono y eficiente).
- El procesamiento del datagrama se hace en una tarea async para no bloquear el event loop.
- Protege contra datagramas demasiado grandes (max_datagram_bytes).

Requisitos:
- Python 3
- motor (MongoDB async driver)
"""

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorClient


def setup_logging(debug: bool) -> None:
    """
    Configura el sistema de logs del RC.

    Args:
        debug (bool):
            Si True activa logs DEBUG.
            Si False deja logs INFO.
    """
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] rc: %(message)s",
    )


def parse_iso_datetime(ts: str) -> Optional[datetime]:
    """
    Convierte un timestamp ISO8601 en datetime.

    El exporter envía timestamps tipo:
        2026-01-19T18:31:47.710Z

    Python no entiende directamente la "Z", por lo que se sustituye por "+00:00".

    Args:
        ts (str):
            Timestamp ISO8601 en string.

    Returns:
        Optional[datetime]:
            datetime parseado si es válido.
            None si no se puede convertir.
    """
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


@dataclass
class RCConfig:
    """
    Configuración del RC cargada desde JSON.

    Attributes:
        udp_host (str):
            IP/host local donde escuchar UDP.
            Ej: "0.0.0.0" para todas las interfaces.

        udp_port (int):
            Puerto UDP donde escuchar.

        max_datagram_bytes (int):
            Tamaño máximo permitido para un datagrama UDP.
            Si llega un datagrama mayor, se descarta por seguridad.

        mongo_uri (str):
            URI de conexión a MongoDB.

        mongo_db (str):
            Base de datos MongoDB donde insertar.

        mongo_collection (str):
            Colección MongoDB donde insertar documentos.
    """
    udp_host: str
    udp_port: int
    max_datagram_bytes: int
    mongo_uri: str
    mongo_db: str
    mongo_collection: str


def load_config(path: str) -> RCConfig:
    """
    Carga la configuración del RC desde un fichero JSON.

    Ejemplo rc_config.json:
    {
      "udp_host": "0.0.0.0",
      "udp_port": 9000,
      "max_datagram_bytes": 4096,
      "mongo_uri": "mongodb://...",
      "mongo_db": "monitoring",
      "mongo_collection": "host_metrics"
    }

    Args:
        path (str):
            Ruta del fichero JSON.

    Returns:
        RCConfig: configuración cargada.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    return RCConfig(
        udp_host=raw.get("udp_host", "0.0.0.0"),
        udp_port=int(raw.get("udp_port", 9000)),
        max_datagram_bytes=int(raw.get("max_datagram_bytes", 4096)),
        mongo_uri=raw["mongo_uri"],
        mongo_db=raw.get("mongo_db", "monitoring"),
        mongo_collection=raw.get("mongo_collection", "host_metrics"),
    )


class UDPCollector(asyncio.DatagramProtocol):
    """
    Implementación de un servidor UDP basado en asyncio.

    Se encarga de:
    - Recibir datagramas UDP
    - Lanzar su procesamiento en una tarea async
    - Validar y guardar en MongoDB si todo es correcto

    Nota:
    DatagramProtocol trabaja con callbacks.
    Por eso se crea una tarea con asyncio.create_task()
    para poder usar 'await' dentro del procesamiento.
    """

    def __init__(self, cfg: RCConfig, col):
        """
        Constructor del protocolo UDP.

        Args:
            cfg (RCConfig):
                Configuración del RC.

            col:
                Colección de MongoDB (motor async collection).
        """
        self.cfg = cfg
        self.col = col

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        """
        Callback llamado automáticamente por asyncio cuando llega un datagrama UDP.

        Args:
            data (bytes):
                Datos recibidos (raw bytes).

            addr (Tuple[str, int]):
                Dirección del cliente (ip, puerto origen).
        """
        logging.debug("Datagram from %s:%d (%d bytes)", addr[0], addr[1], len(data))

        # Procesamos el datagrama en una tarea asíncrona para no bloquear
        asyncio.create_task(self.handle(data, addr))

    async def handle(self, data: bytes, addr: Tuple[str, int]) -> None:
        """
        Procesa un datagrama UDP recibido.

        Flujo:
        1) Validación de tamaño máximo
        2) Parseo JSON
        3) Validación de estructura mínima
        4) Parseo timestamp
        5) Construcción del documento MongoDB
        6) Insert en MongoDB

        Args:
            data (bytes):
                Datagram recibido.

            addr (Tuple[str, int]):
                Dirección del exporter (ip, puerto).
        """
        # 1) Protección contra datagramas demasiado grandes
        if len(data) > self.cfg.max_datagram_bytes:
            logging.warning("Datagram too large from %s (%d bytes)", addr[0], len(data))
            return

        # 2) Parseo JSON
        try:
            obj = json.loads(data.decode("utf-8", errors="ignore"))
        except Exception:
            logging.warning("Invalid JSON from %s", addr[0])
            return

        # 3) Validación mínima del payload
        if not self.valid(obj):
            logging.warning("Invalid payload structure from %s", addr[0])
            logging.debug("Payload received (invalid) = %s", obj)
            return

        # 4) Extraemos server_id (solo identificador, NO autenticación)
        server_id = obj["server_id"]
        logging.info("Packet OK server_id=%s ip=%s", server_id, addr[0])

        # 5) Parseo timestamp
        ts = parse_iso_datetime(obj["ts"])
        if ts is None:
            logging.warning("Invalid timestamp server_id=%s ip=%s ts=%s", server_id, addr[0], obj.get("ts"))
            return

        # Servicios opcionales (apache2, mariadb, ssh, etc.)
        services_data = obj.get("services", {})
        if services_data:
            logging.debug("Services received from %s: %s", server_id, list(services_data.keys()))
        else:
            logging.debug("No services received from %s", server_id)

        # Documento final para MongoDB
        doc = {
            "ts": ts,                    # datetime (mejor para queries por rango)
            "server_id": server_id,      # identificador del host
            "host": {
                "uptime_s": obj["host"].get("uptime_s")
            },
            "metrics": obj["metrics"],   # métricas host (cpu, mem, disks...)
            "services": services_data,   # métricas de servicios (si existen)
        }

        # Insert en MongoDB
        try:
            res = await self.col.insert_one(doc)
            logging.info("Mongo insert OK server_id=%s _id=%s", server_id, res.inserted_id)
        except Exception:
            logging.exception("Mongo insert FAILED server_id=%s", server_id)

    @staticmethod
    def valid(obj: Any) -> bool:
        """
        Valida que el payload recibido tenga una estructura mínima esperada.

        Requisitos mínimos:
        - obj es dict
        - existe server_id
        - existe ts
        - existe host
        - existe metrics

        Args:
            obj (Any):
                Objeto ya parseado desde JSON.

        Returns:
            bool: True si el payload es válido, False si no.
        """
        return (
            isinstance(obj, dict)
            and "server_id" in obj
            and isinstance(obj["server_id"], str)
            and obj["server_id"].strip() != ""
            and "ts" in obj
            and "host" in obj
            and isinstance(obj["host"], dict)
            and "metrics" in obj
            and isinstance(obj["metrics"], dict)
        )


async def main() -> int:
    """
    Punto de entrada asíncrono del RC.

    Flujo:
    1) Parsear CLI (--config, --debug)
    2) Cargar configuración rc_config.json
    3) Conectar a MongoDB y hacer ping
    4) Arrancar servidor UDP en host:port configurado
    5) Mantenerse en ejecución infinita

    Returns:
        int: código de salida (0 ok, 1 fallo en MongoDB).
    """
    parser = argparse.ArgumentParser(description="Recolector Central UDP")
    parser.add_argument("--config", required=True, help="Ruta al fichero rc_config.json")
    parser.add_argument("--debug", action="store_true", help="Activa logs DEBUG")
    args = parser.parse_args()

    setup_logging(args.debug)

    # Cargamos config
    cfg = load_config(args.config)

    logging.info("RC starting on %s:%d", cfg.udp_host, cfg.udp_port)
    logging.info("MongoDB target db=%s collection=%s", cfg.mongo_db, cfg.mongo_collection)

    # Conectamos a MongoDB
    mongo = AsyncIOMotorClient(cfg.mongo_uri)

    # Ping de conexión (si falla, salimos)
    try:
        await mongo.admin.command("ping")
        logging.info("MongoDB connection OK")
    except Exception:
        logging.exception("MongoDB connection FAILED")
        return 1

    # Seleccionamos colección
    col = mongo[cfg.mongo_db][cfg.mongo_collection]

    # Creamos servidor UDP
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: UDPCollector(cfg, col),
        local_addr=(cfg.udp_host, cfg.udp_port),
    )

    logging.info("UDP listening on %s:%d", cfg.udp_host, cfg.udp_port)

    # Nos quedamos "para siempre" escuchando
    try:
        await asyncio.Future()
    finally:
        transport.close()
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
