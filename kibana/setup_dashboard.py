#!/usr/bin/env python3
"""Configure Kibana automatiquement : data view nginx-* puis import du dashboard.

    python kibana/setup_dashboard.py
    python kibana/setup_dashboard.py --kibana http://localhost:5601 --file kibana/dashboard.ndjson

Uniquement la bibliothèque standard : rien à installer.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KIBANA = os.environ.get("KIBANA_URL", f"http://localhost:{os.environ.get('KIBANA_PORT', '5601')}")
DEFAULT_FILE = ROOT / "kibana" / "dashboard.ndjson"

# Identifiant fixe : les visualisations du dashboard.ndjson font référence à cette data view
DATA_VIEW = {
    "id": "nginx",
    "title": "nginx-*",
    "name": "Logs Nginx",
    "timeFieldName": "@timestamp",
}
DASHBOARD_ID = "nginx-log-monitoring"


def request(method, url, body=None, headers=None):
    """Appel HTTP vers Kibana ; l'en-tête kbn-xsrf est obligatoire pour toute écriture."""
    req = urllib.request.Request(url, data=body, method=method, headers={"kbn-xsrf": "true", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def wait_for_kibana(kibana, timeout):
    """Attend que Kibana réponde "available" (il démarre bien après Elasticsearch)."""
    print(f"Attente de Kibana sur {kibana} ...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = request("GET", f"{kibana}/api/status")
            if status["status"]["overall"]["level"] == "available":
                print(" prêt")
                return
        except (urllib.error.URLError, ConnectionError, KeyError, ValueError):
            pass
        print(".", end="", flush=True)
        time.sleep(3)
    sys.exit(f"\nKibana n'est pas prêt après {timeout} s (docker compose ps / docker compose logs kibana)")


def create_data_view(kibana):
    body = json.dumps({"data_view": DATA_VIEW, "override": True}).encode()
    request("POST", f"{kibana}/api/data_views/data_view", body, {"Content-Type": "application/json"})
    # Data view par défaut : Discover l'ouvre directement
    body = json.dumps({"data_view_id": DATA_VIEW["id"], "force": True}).encode()
    request("POST", f"{kibana}/api/data_views/default", body, {"Content-Type": "application/json"})
    print(f"Data view « {DATA_VIEW['name']} » ({DATA_VIEW['title']}) créée")


def import_dashboard(kibana, path):
    """Importe le fichier ndjson via l'API saved objects (envoi multipart/form-data)."""
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: application/ndjson\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    result = request(
        "POST",
        f"{kibana}/api/saved_objects/_import?overwrite=true",
        body,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    if not result.get("success"):
        sys.exit(f"Échec de l'import : {json.dumps(result.get('errors'), indent=2, ensure_ascii=False)}")
    print(f"{result['successCount']} objets importés depuis {path.name}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Crée la data view nginx-* et importe le dashboard Kibana.")
    p.add_argument("--kibana", default=DEFAULT_KIBANA, help=f"URL de Kibana (défaut : {DEFAULT_KIBANA})")
    p.add_argument("--file", type=Path, default=DEFAULT_FILE, help="fichier ndjson à importer")
    p.add_argument("--timeout", type=int, default=180, help="attente maximale de Kibana, en secondes")
    args = p.parse_args()

    kibana = args.kibana.rstrip("/")
    try:
        wait_for_kibana(kibana, args.timeout)
        create_data_view(kibana)
        import_dashboard(kibana, args.file)
    except urllib.error.HTTPError as e:
        sys.exit(f"Erreur HTTP {e.code} sur {e.url} : {e.read().decode(errors='replace')}")
    print(f"\nDashboard : {kibana}/app/dashboards#/view/{DASHBOARD_ID}")


if __name__ == "__main__":
    main()
