from flask import Flask, Response
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

# Fichier JSON pour persister les iDRAC ajoutés
IDRAC_FILE = 'idracs.json'

# Chargement initial des iDRAC
if os.path.exists(IDRAC_FILE):
    with open(IDRAC_FILE, 'r') as f:
        idracs = json.load(f)
else:
    idracs = []

# Cache des métriques
cached_metrics = {}

def save_idracs():
    with open(IDRAC_FILE, 'w') as f:
        json.dump(idracs, f)

# MODULE: État général du serveur
def get_idrac_server_status(idrac_ip, site, username, password):
    base_url = f'https://{idrac_ip}/redfish/v1/'
    url = base_url + 'Systems/System.Embedded.1'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False, timeout=10)
    metrics = []

    if response.status_code == 200:
        data = response.json()
        status = data.get('Status', {})
        state = status.get('State', 'Inconnu')
        health = status.get('Health', 'Inconnu')

        metrics.append(f'idrac_server_status{{idrac_ip="{idrac_ip}", site="{site}", state="{state}", health="{health}"}} 1')
    else:
        metrics.append(f'idrac_exporter_error{{idrac_ip="{idrac_ip}", site="{site}", error="Failed to fetch server status"}} 1')

    return "\n".join(metrics) + "\n"

# MODULE: Ventilateurs (fans)
def get_idrac_fan_info(idrac_ip, site, username, password):
    base_url = f'https://{idrac_ip}/redfish/v1/'
    url = base_url + 'Chassis/System.Embedded.1/Thermal'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False, timeout=10)
    metrics = []

    if response.status_code == 200:
        data = response.json()
        for fan in data.get('Fans', []):
            name = fan.get('Name', 'Inconnu').replace(" ", "_").lower()
            reading = fan.get('Reading', 0)
            state = fan.get('Status', {}).get('State', 'Inconnu')
            health = fan.get('Status', {}).get('Health', 'Inconnu')

            metrics.append(
                f'idrac_fan_info{{idrac_ip="{idrac_ip}", site="{site}", name="{name}", state="{state}", health="{health}"}} {reading}'
            )
    else:
        metrics.append(f'idrac_exporter_error{{idrac_ip="{idrac_ip}", site="{site}", error="Failed to fetch fan info"}} 1')

    return "\n".join(metrics) + "\n"

#MODULE ETAT GENERAL SANTE
def get_idrac_summary_component_health(idrac_ip, site, username, password):
    base_url = f'https://{idrac_ip}/redfish/v1/'
    url = base_url + 'Systems/System.Embedded.1'
    response = requests.get(url, auth=HTTPBasicAuth(username, password), verify=False, timeout=10)
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
        data = response.json()
        status = data.get('Status', {})
        state = status.get('State', 'Unknown')
        health = status.get('Health', 'Unknown')
        value = status_map.get(health, 4)

        # Ajoute le statut global
        metrics.append(
            f'idrac_system_status{{idrac_ip="{idrac_ip}", site="{site}", state="{state}", health="{health}"}} {value}'
        )

        # Résumé de certains composants si disponible
        # Sur iDRAC, il n'y a pas de "AggregateHealthStatus" comme sur iLO
        # On va donc interroger directement les composants majeurs (Power, Thermal, Storage)

        components = [
            {"name": "PowerSupplies", "endpoint": "/redfish/v1/Chassis/System.Embedded.1/Power"},
            {"name": "Fans", "endpoint": "/redfish/v1/Chassis/System.Embedded.1/Thermal"},
            {"name": "Storage", "endpoint": "/redfish/v1/Systems/System.Embedded.1/Storage"}
        ]

        for comp in components:
            r = requests.get(f"https://{idrac_ip}{comp['endpoint']}",
                             verify=False, auth=(username, password), timeout=10)
            if r.status_code == 200:
                comp_data = r.json()
                # Cherche un health global (souvent 'Status' à la racine)
                health_status = comp_data.get('Status', {}).get('Health', 'Unknown')
                health_value = status_map.get(health_status, 4)
                metrics.append(
                    f'idrac_summary_component_health{{idrac_ip="{idrac_ip}", site="{site}", component="{comp["name"]}", health="{health_status}"}} {health_value}'
                )
            else:
                metrics.append(
                    f'idrac_exporter_error{{idrac_ip="{idrac_ip}", site="{site}", error="Failed to fetch {comp["name"]} health"}} 1'
                )
    else:
        metrics.append(
            f'idrac_exporter_error{{idrac_ip="{idrac_ip}", site="{site}", error="Failed to fetch system summary"}} 1'
        )

    return "\n".join(metrics) + "\n"



# BOUCLE D’UPDATE ----------------------------------------------------

def update_metrics_loop():
    while True:
        try:
            for idrac in idracs:
                ip = idrac['ip']
                site = idrac['site']
                username = idrac['username']
                password = idrac['password']

                cached_metrics[f'{ip}_server_status'] = get_idrac_server_status(ip, site, username, password)
                cached_metrics[f'{ip}_fan_info'] = get_idrac_fan_info(ip, site, username, password)
                cached_metrics[f'{ip}_summary_component_health'] = get_idrac_summary_component_health(ip, site, username, password)


        except Exception as e:
            error_msg = f'# Error fetching metrics: {str(e)}\n'
            for key in cached_metrics:
                cached_metrics[key] = error_msg

        time.sleep(60)

threading.Thread(target=update_metrics_loop, daemon=True).start()

# ENDPOINT /metrics --------------------------------------------------

@app.route('/metrics')
def metrics():
    output = ""
    for value in cached_metrics.values():
        output += value
    return Response(output, mimetype='text/plain')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8001)

