# 🚀 PFC DAM: Sistema de Gestión y Monitorización GNU/Linux

Este proyecto final de ciclo para Desarrollo de Aplicaciones Multiplataforma (DAM) consiste en una infraestructura completa para la administración y supervisión en tiempo real de parques de servidores GNU/Linux a gran escala.

---

## 🏗️ Arquitectura del Sistema

El proyecto implementa un modelo de **3 capas** diseñado para la alta disponibilidad y escalabilidad.

### 1. Infraestructura y Despliegue
* **Virtualización:** Nodo central en **Proxmox** gestionando **50 contenedores LXC** (Debian 13) que simulan el parque de servidores (Apache, SSH, MariaDB).
* **Orquestación:** Despliegue y configuración automatizada mediante **Ansible**.
* **Balanceo de Carga:** **Nginx** actuando como Proxy Inverso y Balanceador hacia 3 instancias de **Apache TomEE**, garantizando comunicaciones seguras mediante **HTTPS**.

### 2. Capa de Datos (Backend)
* **API:** Desarrollada en **Java** sobre **Apache TomEE** (Requisito técnico).
* **Persistencia:** * **MariaDB:** Datos relacionales de gestión.
    * **MongoDB:** Almacenamiento de documentos JSON con métricas históricas.
    * **MinIO:** Almacenamiento de objetos.

### 3. Capa de Cliente
* **Desktop:** Aplicación nativa en **Java Swing**.
* **Mobile:** Aplicación multiplataforma desarrollada en **Flutter/Dart**.

---

## Repositorios del Proyecto
Para facilitar la modularidad, el código se divide en:

* **Backend (API)**: [metrics-servers-pfc](https://github.com/alejandrogb26/metrics-servers-pfc)
* **App Móvil (Flutter)**: []()
* **App Desktop (Swing)**: []()
* **Core (Este repo)**: Scripts de los Exporters, Recolector Central (RC) y archivos de inicilización de DBs.

---

## 🛠️ Componentes de Monitorización

Este repositorio contiene la lógica de recolección de datos:

* **Exporters (.py):** Agentes ligeros que se ejecutan en los nodos finales. Extraen métricas del sistema y las envían como paquetes **JSON vía UDP** para minimizar el overhead de red.
* **Recolector Central (RC):** Servicio encargado de escuchar el tráfico UDP, procesar los JSON entrantes e insertarlos de forma eficiente en la base de datos NoSQL (MongoDB).

---

## 🚀 Guía de Inicio Rápido (Debian 13)

### Requisitos previos
* Python 3.11+
* Acceso de red entre nodos (Puerto UDP configurado)
* Instancias de MongoDB y MariaDB operativas

### Instalación de Exporters y RC

1. **Clonar el repositorio:**
   ```bash
   git clone [https://github.com/alejandrogb26/metrics-servers-pfc.git](https://github.com/alejandrogb26/metrics-servers-pfc.git)
   cd metrics-servers-pfc
    ```
1. **Inicializar bases de datos**

2. **Lanzar el Recolector Central (RC)**
   ```bash
   # Actualizar el sistema.
   apt update && apt full-upgrade -y && apt autoremove -y && apt autoclean -y

   # Instalar Python
   apt install -y python3 python3-venv
   
   # Crear un usuario específico para el RC.
   useradd --system --no-create-home --shell /usr/bin/nologin rc_user

   # Crear la estructura de directorios
   mkdir /etc/rc

   # Crear el entorno virtual de Python
   python3 -m venv /etc/rc/venv

   # Copiar el fichero rc.py y rc_config.json (acúerdate de modificarlo).

   # Configurar los permisos y propietarios correctamente
   chown -R rc_user:rc_user /etc/rc
   chmod -R 755 /etc/rc

   # Crear el servicio para RC.
   /etc/systemd/system/rc.service

   [Unit]
   Description=Monitoring Recolector Central (UDP -> MongoDB)
   After=network-online.target
   Wants=network-online.target
   
   [Service]
   Type=simple
   User=rc_user
   Group=rc_user
   WorkingDirectory=/etc/rc
   
   ExecStart=/etc/monitoring/venv/bin/python \
     /etc/rc/rc.py \
     --config /etc/rc/rc_config.json

   #Restart=always
   #RestartSec=5

   # Logs
   StandardOutput=journal
   StandardError=journal
   
   [Install]
   WantedBy=multi-user.target

   # Configurar el arranque automático
   systemctl daemon-reload
   systemctl enable --now rc.service
    ```
   
3. **Lanzar el exporter en los servidores**
   ```bash
   # Actualizar el sistema.
   apt update && apt full-upgrade -y && apt autoremove -y && apt autoclean -y

   # Instalar Python
   apt install -y python3 python3-venv
   
   # Crear un usuario específico para el RC.
   useradd --system --no-create-home --shell /usr/bin/nologin monitoring_user

   # Crear la estructura de directorios
   mkdir /etc/monitoring

   # Crear el entorno virtual de Python
   python3 -m venv /etc/monitoring/venv

   # Copiar el fichero exporter.py y los .py para los distintos servicios a monitorizar.
   # Además, debes crear el fichero 'server_id' en '/etc/monitoring' con el ID del servidor. Este es el que aparecerá en MongoDB.

   # Configurar los permisos y propietarios correctamente
   chown -R monitoring_user:monitoring_user /etc/monitoring
   chmod -R 755 /etc/monitoring

   # Crear el servicio para RC.
   /etc/systemd/system/monitoring.service

   [Unit]
   Description=Monitoring Exporter (UDP)
   After=network-online.target
   Wants=network-online.target
   
   [Service]
   Type=simple
   User=monitoring_user
   Group=monitoring_user
   WorkingDirectory=/etc/monitoring
   
   ExecStart=/etc/monitoring/venv/bin/python \
     /etc/monitoring/exporter.py \
       --rc-host <IP_RC> \
       --rc-port 9000 \
       --services [apache2 | mariadb | ssh] \
       --interval [x segundos]

   #Restart=always
   #RestartSec=5

   # Logs
   StandardOutput=journal
   StandardError=journal
   
   [Install]
   WantedBy=multi-user.target

   # Configurar el arranque automático
   systemctl daemon-reload
   systemctl enable --now monitoring.service
    ```
