"""
=======================================
SCRIPT : multi_ilo_web.py (exporter Prometheus)
=======================================

DESCRIPTION :
-------------
Ce script Flask expose des métriques matérielles (Redfish/iLO) au format Prometheus, 
afin de permettre un monitoring centralisé avec Grafana. Les métriques sont extraites 
régulièrement depuis une liste de serveurs iLO définis dans le fichier `ilos.json`.

Le script interroge l'API Redfish de chaque iLO pour collecter des informations sur :
- l'état global du système,
- les CPU,
- la mémoire,
- la température (CPU, Inlet, etc.),
- les ventilateurs,
- la batterie SmartStorage,
- l'alimentation électrique,
- les contrôleurs/disques de stockage,
- les périphériques PCI.

Les résultats sont mis en cache toutes les 60 secondes pour ne pas surcharger les iLO.

FONCTIONNEMENT :
----------------
1. Le script démarre un serveur Flask sur le port 8000.
2. À l'adresse `/metrics`, Prometheus peut récupérer les données mises en cache.
3. Un thread de fond interroge chaque iLO en boucle toutes les 60 secondes.
4. Les erreurs de communication sont également renvoyées comme métriques (préfixe `ilo_exporter_error`).

FICHIERS ASSOCIÉS :
-------------------
- `ilos.json` : contient la liste des iLO à interroger (IP, site, identifiants)
  Format :
  [
      {
          "ip": "10.101.50.30",
          "site": "site1",
          "username": "admin",
          "password": "motdepasse"
      },
      ...
  ]

DEPENDANCES :
-------------
- Python 3.x
- Flask
- prometheus_client
- requests
- urllib3

INSTALLATION DES LIBRAIRIES :
-----------------------------
pip install flask prometheus_client requests urllib3

UTILISATION :
-------------
python multi_ilo_web.py

Puis, dans Prometheus, ajouter une cible vers :
    http://<IP ou hostname>:8000/metrics

EXEMPLE DE MÉTRIQUES EXPOSÉES :
-------------------------------
- ilo_cpu_info{ilo_ip="10.101.50.30", site="site1", model="Intel Xeon", state="Enabled", health="OK"} 1
- ilo_temperature_celsius{ilo_ip="10.101.50.30", site="site1", sensor="cpu", name="cpu_1"} 42
- ilo_memory_total_gb{ilo_ip="10.101.50.30", site="site1"} 128
- ilo_exporter_error{ilo_ip="10.101.50.40", site="site2", error="Failed to fetch system summary"} 1

AUTEUR :
--------
Développé dans le cadre d’un projet de supervision de serveurs iLO avec Grafana.

Dernière mise à jour : juin 2025

"""



from flask import Flask, Response, request, redirect
from prometheus_client import Gauge
import threading
import time
import requests
from requests.auth import HTTPBasicAuth
import urllib3
import json
import os

app = Flask(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Fichier JSON pour persister les iLO ajoutés
ILO_FILE = '/home/andrea/project/ilos.json'

# Chargement initial des iLO
if os.path.exists(ILO_FILE):
    with open(ILO_FILE, 'r') as f:
        ilos = json.load(f)
else:
    ilos = [
        
    ]

# Cache des métriques
cached_metrics = {}

def save_ilos():
    with open(ILO_FILE, 'w') as f:
        json.dump(ilos, f)



def get_server_status(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    url = base_url + 'Systems/1'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False)
    metrics = []

    if response.status_code == 200:
        system_data = response.json()
        status = system_data.get('Status', {})
        state = status.get('State', 'Inconnu')
        health = status.get('Health', 'Inconnu')
        metrics.append(f'ilo_server_status{{ilo_ip="{ilo_ip}", site="{site}", state="{state}", health="{health}"}} 1')
    else:
        metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Failed to fetch server status"}} 1')

    return "\n".join(metrics) + "\n"


def get_cpu_info(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    url = base_url + 'Systems/1/Processors'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False)
    metrics = []

    if response.status_code == 200:
        cpu_data = response.json()
        for cpu_member in cpu_data.get('Members', []):
            cpu_url = f"https://{ilo_ip}{cpu_member['@odata.id']}"
            cpu_response = requests.get(cpu_url, auth=HTTPBasicAuth(username, password), verify=False)
            if cpu_response.status_code == 200:
                cpu_details = cpu_response.json()
                health = cpu_details.get("Status", {}).get("Health", "Inconnu")
                state = cpu_details.get("Status", {}).get("State", "Inconnu")
                if state != "Absent":
                    model = cpu_details.get('Model', 'Inconnu')
                    metrics.append(
                        f'ilo_cpu_info{{ilo_ip="{ilo_ip}", site="{site}", model="{model}", state="{state}", health="{health}"}} 1'
                    )
            else:
                metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="CPU member access failed"}} 1')
    else:
        metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="CPU endpoint access failed"}} 1')

    return "\n".join(metrics) + "\n"

def get_temperature_info(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    url = base_url + 'Chassis/1/Thermal'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False)
    metrics = []

    if response.status_code == 200:
        data = response.json()
        for sensor in data.get('Temperatures', []):
            name = sensor.get('Name', 'Unknown')
            reading_celsius = sensor.get('ReadingCelsius')
            state = sensor.get('Status', {}).get('State', 'Inconnu')
            health = sensor.get('Status', {}).get('Health', 'Inconnu')

            # Filtrage des capteurs spécifiques
            if reading_celsius is not None:
                name_clean = name.replace(" ", "_").lower()
                if 'CPU' in name:
                    sensor_type = "cpu"
                elif 'Inlet' in name:
                    sensor_type = "inlet"
                elif 'Ambient' in name:
                    sensor_type = "ambient"
                elif 'Chipset' in name:
                    sensor_type = "chipset"
                elif 'BMC' in name:
                    sensor_type = "bmc"
                else:
                    continue

                metrics.append(
                    f'ilo_temperature_celsius{{ilo_ip="{ilo_ip}", site="{site}", sensor="{sensor_type}", name="{name_clean}"}} {reading_celsius}'
                )
    else:
        metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Failed to fetch temperature info"}} 1')

    return "\n".join(metrics) + "\n"



def get_system_summary(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    url = base_url + 'Systems/1'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False)
    metrics = []

    status_map = {
        "OK": 0,
        "Warning": 1,
        "Critical": 2,
        "Absent": 3,
        "Unknown": 4,
        "Disabled": 5,
        "Enabled": 0
    }

    if response.status_code == 200:
        system_data = response.json()
        status = system_data.get('Status', {})
        state = status.get('State', 'Unknown')
        health = status.get('Health', 'Unknown')
        value = status_map.get(health, 4)
        metrics.append(f'ilo_system_status{{ilo_ip="{ilo_ip}", site="{site}", state="{state}", health="{health}"}} {value}')

        oem_info = system_data.get("Oem", {}).get("Hp", {}) or system_data.get("Oem", {}).get("Hpe", {})
        summary = oem_info.get("AggregateHealthStatus", {})

        if summary:
            for key, val in summary.items():
                if key not in ['AgentlessManagementService', 'BiosOrHardwareHealth', 'FanRedundancy', 'PowerSupplyRedundancy']:
                    health_val = val.get("Status", {}).get("Health", "Unknown")
                    num = status_map.get(health_val, 4)
                    metrics.append(f'ilo_summary_component_health{{ilo_ip="{ilo_ip}", site="{site}", component="{key}", health="{health_val}"}} {num}')
        else:
            metrics.append(f'ilo_oem_summary_missing{{ilo_ip="{ilo_ip}", site="{site}"}} 1')
    else:
        metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Failed to fetch system summary"}} 1')

    return "\n".join(metrics) + "\n"

def get_server_identification_info(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    url = base_url + 'Systems/1/'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False)
    metrics = []

    if response.status_code == 200:
        data = response.json()

        product_name = data.get('Model', 'Unknown')
        serial_number = data.get('SerialNumber', 'Unknown')
        product_id = data.get('SKU', 'Unknown')

        metrics.append(f'ilo_server_info{{ilo_ip="{ilo_ip}", site="{site}", field="product_name", value="{product_name}"}} 1')
        metrics.append(f'ilo_server_info{{ilo_ip="{ilo_ip}", site="{site}", field="serial_number", value="{serial_number}"}} 1')
        metrics.append(f'ilo_server_info{{ilo_ip="{ilo_ip}", site="{site}", field="product_id", value="{product_id}"}} 1')
    else:
        metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Failed to fetch server identification info"}} 1')

    return "\n".join(metrics) + "\n"

def get_power_summary(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    url = base_url + 'Chassis/1/Power'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False)
    metrics = []

    if response.status_code == 200:
        try:
            data = response.json()
            power_reading = data.get("PowerControl", [{}])[0].get("PowerConsumedWatts", 0)
            power_status = data.get("Redundancy", [{}])[0].get("Mode", "Unknown")

            metrics.append(
                f'ilo_power_consumption_watts{{ilo_ip="{ilo_ip}", site="{site}", power_status="{power_status}"}} {power_reading}'
            )
        except Exception as e:
            metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Exception parsing power summary: {e}"}} 1')
    else:
        metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Failed to fetch power summary"}} 1')

    return "\n".join(metrics) + "\n"


def get_power_redundancy_status(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    url = base_url + 'Chassis/1/Power'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False)
    metrics = []

    if response.status_code == 200:
        try:
            data = response.json()
            redundancy_status = data.get("Redundancy", [{}])[0].get("Status", {}).get("State", "Unknown")
            redundancy_value = 0 if redundancy_status == "Enabled" else 1

            metrics.append(
                f'ilo_power_redundancy_status{{ilo_ip="{ilo_ip}", site="{site}", redundant="{redundancy_value}"}} {redundancy_value}'
            )
        except Exception as e:
            metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Exception parsing power redundancy info: {e}"}} 1')
    else:
        metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Failed to fetch power redundancy info"}} 1')

    return "\n".join(metrics) + "\n"




def get_ilo_storage_info(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    metrics = []

    controllers_url = base_url + 'Systems/1/SmartStorage/ArrayControllers'
    response = requests.get(controllers_url, auth=HTTPBasicAuth(username, password), verify=False)

    if response.status_code != 200:
        metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Storage controller access error {response.status_code}"}} 1')
        return "\n".join(metrics) + "\n"

    controllers = response.json().get('Members', [])
    for ctrl in controllers:
        ctrl_url = f"https://{ilo_ip}{ctrl['@odata.id']}"
        ctrl_resp = requests.get(ctrl_url, auth=HTTPBasicAuth(username, password), verify=False)
        if ctrl_resp.status_code != 200:
            metrics.append(f'ilo_storage_controller_error{{ilo_ip="{ilo_ip}", site="{site}", controller="{ctrl["@odata.id"]}"}} 1')
            continue

        controller_data = ctrl_resp.json()
        model = controller_data.get('Model', 'Inconnu')
        status = controller_data.get('Status', {}).get('Health', 'Inconnu')
        metrics.append(f'ilo_storage_controller_status{{ilo_ip="{ilo_ip}", site="{site}", model="{model}", health="{status}"}} 1')

        # Disques physiques
        physical_url = controller_data.get("Links", {}).get("PhysicalDrives", {}).get("@odata.id")
        if physical_url:
            phys_resp = requests.get(f"https://{ilo_ip}{physical_url}", auth=HTTPBasicAuth(username, password), verify=False)
            if phys_resp.status_code == 200:
                for member in phys_resp.json().get('Members', []):
                    disk_url = f"https://{ilo_ip}{member['@odata.id']}"
                    disk_resp = requests.get(disk_url, auth=HTTPBasicAuth(username, password), verify=False)
                    if disk_resp.status_code == 200:
                        disk = disk_resp.json()
                        loc = disk.get('Location', 'Inconnu')
                        model = disk.get('Model', 'Inconnu')
                        capacity = disk.get('CapacityMiB', 'N/A')
                        if isinstance(capacity, (int, float)):
                            capacity_gb = capacity / 1024
                        else:
                            capacity_gb = 0
                        health = disk.get('Status', {}).get('Health', 'Inconnu')
                        metrics.append(
                            f'ilo_storage_physical_disk{{ilo_ip="{ilo_ip}", site="{site}", location="{loc}", model="{model}", capacity_gb="{capacity_gb:.2f}", health="{health}"}} 1'
                        )

        # Disques logiques
        logical_url = controller_data.get("Links", {}).get("LogicalDrives", {}).get("@odata.id")
        if logical_url:
            log_resp = requests.get(f"https://{ilo_ip}{logical_url}", auth=HTTPBasicAuth(username, password), verify=False)
            if log_resp.status_code == 200:
                for member in log_resp.json().get('Members', []):
                    log_url = f"https://{ilo_ip}{member['@odata.id']}"
                    ld_resp = requests.get(log_url, auth=HTTPBasicAuth(username, password), verify=False)
                    if ld_resp.status_code == 200:
                        ld = ld_resp.json()
                        size = ld.get('CapacityMiB', 'N/A')
                        if isinstance(size, (int, float)):
                            size_gb = size / 1024
                        else:
                            size_gb = 0
                        raid = ld.get('Raid', 'N/A')
                        health = ld.get('Status', {}).get('Health', 'Inconnu')
                        metrics.append(
                            f'ilo_storage_logical_disk{{ilo_ip="{ilo_ip}", site="{site}", raid="{raid}", capacity_gb="{size_gb:.2f}", health="{health}"}} 1'
                        )

    return "\n".join(metrics) + "\n"


def get_fan_info(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    url = base_url + 'Chassis/1/Thermal'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False)
    metrics = []

    if response.status_code == 200:
        thermal_data = response.json()
        for fan in thermal_data.get('Fans', []):
            name = fan.get('Name', 'Inconnu').replace(" ", "_").lower()
            speed = fan.get('Reading', 0)
            state = fan.get('Status', {}).get('State', 'Inconnu')
            health = fan.get('Status', {}).get('Health', 'Inconnu')

            metrics.append(
                f'ilo_fan_info{{ilo_ip="{ilo_ip}", site="{site}", name="{name}", state="{state}", health="{health}"}} {speed}'
            )
    else:
        metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Failed to fetch fan info"}} 1')

    return "\n".join(metrics) + "\n"


def get_smartstorage_battery_status(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    url = base_url + 'Chassis/1'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False)
    metrics = []

    if response.status_code == 200:
        chassis_data = response.json()
        smart_storage_battery = chassis_data.get('Oem', {}).get('Hpe', {}).get('SmartStorageBattery', [])

        if smart_storage_battery:
            for battery in smart_storage_battery:
                serial_number = battery.get('SerialNumber', 'Inconnu')
                health = battery.get('Status', {}).get('Health', 'Inconnu')
                health_value = 1 if health == "OK" else 0
                metrics.append(
                    f'ilo_smart_battery_status{{ilo_ip="{ilo_ip}", site="{site}", serial="{serial_number}", health="{health}"}} {health_value}'
                )
        else:
            metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="SmartStorageBattery info unavailable"}} 1')
    else:
        metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="SmartStorageBattery access error {response.status_code}"}} 1')

    return "\n".join(metrics) + "\n"

def get_memory_info(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    url = base_url + 'Systems/1/Memory'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False)
    metrics = []
    total_capacity = 0  # Capacité totale en GB

    if response.status_code == 200:
        memory_data = response.json()
        for mem in memory_data.get('Members', []):
            mem_url = f"https://{ilo_ip}{mem['@odata.id']}"
            mem_response = requests.get(mem_url, auth=HTTPBasicAuth(username, password), verify=False)
            if mem_response.status_code == 200:
                mem_details = mem_response.json()
                capacity_mib = mem_details.get("CapacityMiB")
                if capacity_mib:
                    total_capacity += round(capacity_mib / 1024, 2)
    else:
        metrics.append(f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Memory endpoint access failed"}} 1')
        return "\n".join(metrics) + "\n"

    metrics.append(f'ilo_memory_total_gb{{ilo_ip="{ilo_ip}", site="{site}"}} {total_capacity}')
    return "\n".join(metrics) + "\n"


def get_device_info(ilo_ip, site, username, password):
    base_url = f'https://{ilo_ip}/redfish/v1/'
    url = base_url + 'Systems/1/PCIDevices'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False, timeout=10)
    metrics = []

    if response.status_code == 200:
        device_data = response.json()
        members = device_data.get('Members', [])

        for member in members:
            device_url = f"https://{ilo_ip}{member['@odata.id']}"
            device_response = requests.get(device_url, auth=HTTPBasicAuth(username, password), verify=False, timeout=10)

            if device_response.status_code == 200:
                device = device_response.json()
                location = device.get("LocationString", "unknown").replace(" ", "_")
                name = device.get("Name", "unknown").replace(" ", "_")

                metrics.append(
                    f'ilo_device_info{{ilo_ip="{ilo_ip}", site="{site}", location="{location}", name="{name}"}} 1'
                )
            else:
                metrics.append(
                    f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Failed to fetch device {member["@odata.id"]}"}} 1'
                )
    else:
        metrics.append(
            f'ilo_exporter_error{{ilo_ip="{ilo_ip}", site="{site}", error="Failed to fetch PCI devices list"}} 1'
        )

    return "\n".join(metrics) + "\n"



def update_metrics_loop():
    while True:
        try:
            for ilo in ilos:
                ip = ilo['ip']
                site = ilo['site']
                username = ilo['username']
                password = ilo['password']
                cached_metrics[f'{ip}_server_status'] = get_server_status(ip, site, username, password)
                cached_metrics[f'{ip}_system_summary'] = get_system_summary(ip, site, username, password)
                cached_metrics[f'{ip}_identification'] = get_server_identification_info(ip, site, username, password)
                cached_metrics[f'{ip}_cpu'] = get_cpu_info(ip, site, username, password)
                cached_metrics[f'{ip}_battery_status'] = get_smartstorage_battery_status(ip, site, username, password)
                cached_metrics[f'{ip}_memory_total'] = get_memory_info(ip, site, username, password)
                cached_metrics[f'{ip}_storage_info'] = get_ilo_storage_info(ip, site, username, password)
                cached_metrics[f'{ip}_power_summary'] = get_power_summary(ip, site, username, password)
                cached_metrics[f'{ip}_power_redundancy'] = get_power_redundancy_status(ip, site, username, password)
                cached_metrics[f'{ip}_fan_info'] = get_fan_info(ip, site, username, password)
                cached_metrics[f'{ip}_temperature_info'] = get_temperature_info(ip, site, username, password)
                cached_metrics[f'{ip}_device_info'] = get_device_info(ip, site, username, password)
        except Exception as e:
            error_msg = f'# Error fetching metrics: {str(e)}\n'
            for key in cached_metrics:
                cached_metrics[key] = error_msg
        time.sleep(60)

threading.Thread(target=update_metrics_loop, daemon=True).start()

@app.route('/metrics')
def metrics():
    output = ""
    for value in cached_metrics.values():
        output += value
    return Response(output, mimetype='text/plain')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
