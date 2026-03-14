# services/mariadb.py
from typing import Dict, Any

import pymysql  # pip install pymysql


def fetch_mariadb_status(
    host: str = "127.0.0.1",
    port: int = 3306,
    user: str = "monitoring",
    password: str = "",
    timeout: float = 1.5,
) -> Dict[str, Any]:
    """
    Obtiene métricas básicas de MySQL/MariaDB (RECORTADO).

    Recorte aplicado:
    - Eliminamos campos de ruido:
        host, port, user, version, db_type, variables (max_connections, etc.)
    - Nos quedamos con métricas de salud/rendimiento:
        uptime, threads, conexiones, queries, traffic, innodb
    """

    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            connect_timeout=timeout,
            read_timeout=timeout,
            write_timeout=timeout,
            cursorclass=pymysql.cursors.DictCursor,
        )
    except Exception as e:
        return {
            "enabled": False,
            "error": f"cannot_connect: {e}",
        }

    try:
        with conn.cursor() as cur:
            cur.execute("SHOW GLOBAL STATUS")
            status_rows = cur.fetchall()
            status = {r["Variable_name"]: r["Value"] for r in status_rows}

    except Exception as e:
        return {
            "enabled": False,
            "error": f"query_failed: {e}",
        }
    finally:
        conn.close()

    def to_int(v):
        try:
            return int(v)
        except Exception:
            return None

    return {
        "enabled": True,

        "uptime_s": to_int(status.get("Uptime")),

        "threads": {
            "connected": to_int(status.get("Threads_connected")),
            "running": to_int(status.get("Threads_running")),
            "cached": to_int(status.get("Threads_cached")),
        },

        "connections": {
            "max_used": to_int(status.get("Max_used_connections")),
            "aborted_clients": to_int(status.get("Aborted_clients")),
            "aborted_connects": to_int(status.get("Aborted_connects")),
        },

        "queries": {
            "queries_total": to_int(status.get("Queries")),
            "questions": to_int(status.get("Questions")),
            "slow_queries": to_int(status.get("Slow_queries")),
        },

        "traffic": {
            "bytes_received": to_int(status.get("Bytes_received")),
            "bytes_sent": to_int(status.get("Bytes_sent")),
        },

        "innodb": {
            "buffer_pool_pages_total": to_int(status.get("Innodb_buffer_pool_pages_total")),
            "buffer_pool_pages_free": to_int(status.get("Innodb_buffer_pool_pages_free")),
            "buffer_pool_pages_dirty": to_int(status.get("Innodb_buffer_pool_pages_dirty")),
        },
    }
