#!/usr/bin/env python3
"""Générateur de logs Nginx au format JSON pour la stack ELK.

Le format est identique au `log_format json_logs` du projet microservices-ha-cluster,
donc un seul pipeline Logstash traite les deux sources.

Exemples :
    python generator/generate_logs.py                    # 500 requêtes réparties sur 30 min
    python generator/generate_logs.py --follow --rate 5  # trafic en continu (Ctrl+C pour arrêter)
    python generator/generate_logs.py --attack           # force brute sur POST /login
    python generator/generate_logs.py --source ../microservices-ha-cluster/logs/access.log

Uniquement la bibliothèque standard : rien à installer.
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "logs" / "access.log"
DEFAULT_SOURCE = os.environ.get(
    "MICROSERVICES_LOGS", str(ROOT.parent / "microservices-ha-cluster" / "logs" / "access.log")
)

# Adresses issues des plages réservées à la documentation (RFC 5737) : aucune vraie IP.
CLIENT_IPS = [f"192.0.2.{i}" for i in range(10, 25)] + [f"198.51.100.{i}" for i in range(30, 45)]
# Quelques clients font beaucoup de requêtes, la plupart peu (distribution réaliste)
CLIENT_WEIGHTS = [1 / (rank + 1) for rank in range(len(CLIENT_IPS))]
DEFAULT_ATTACKER_IP = "203.0.113.66"

USER_AGENTS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36", 35),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0", 15),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15", 12),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1", 15),
    ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36", 12),
    ("curl/8.5.0", 4),
    ("python-requests/2.32.3", 3),
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", 4),
]
ATTACK_USER_AGENT = "Mozilla/4.0 (Hydra)"

# (méthode, URL, poids, {code HTTP: poids}, temps de réponse typique en s, passe par l'upstream)
ROUTES = [
    ("GET", "/", 20, {200: 95, 304: 5}, 0.004, True),
    ("GET", "/api/products", 18, {200: 96, 304: 2, 500: 1, 502: 1}, 0.030, True),
    ("GET", "/api/products/{id}", 15, {200: 90, 404: 8, 500: 2}, 0.020, True),
    ("GET", "/api/search?q={word}", 4, {200: 90, 500: 5, 504: 5}, 0.150, True),
    ("POST", "/api/orders", 8, {201: 88, 400: 6, 500: 4, 503: 2}, 0.080, True),
    ("GET", "/api/orders/{id}", 6, {200: 85, 404: 10, 500: 5}, 0.025, True),
    # Quelques utilisateurs légitimes se trompent de mot de passe (401 isolés)
    ("POST", "/login", 5, {200: 80, 401: 20}, 0.060, True),
    ("GET", "/static/{asset}", 15, {200: 80, 304: 20}, 0.001, False),
    ("GET", "/health", 8, {200: 100}, 0.001, True),
    # Scans automatiques de chemins sensibles : toujours en 404
    ("GET", "/{probe}", 3, {404: 100}, 0.001, False),
]
ASSETS = ["app.js", "style.css", "logo.svg", "vendor.js", "favicon.ico"]
WORDS = ["laptop", "clavier", "ecran", "souris", "casque", "routeur"]
PROBES = ["wp-admin", ".env", "phpmyadmin", ".git/config", "admin.php"]
UPSTREAMS = ["api1:8000", "api2:8000", "api3:8000"]


def now():
    return datetime.now().astimezone()


def fill_uri(template):
    return template.format(
        id=random.randint(1, 500),
        word=random.choice(WORDS),
        asset=random.choice(ASSETS),
        probe=random.choice(PROBES),
    )


def weighted_choice(weights):
    return random.choices(list(weights), weights=list(weights.values()))[0]


def make_entry(ts, ip, method, uri, status, base_time, upstream, user_agent):
    """Construit une ligne identique à celle qu'écrirait Nginx (log_format json_logs)."""
    # Temps de réponse log-normal : la plupart rapides, quelques requêtes lentes
    request_time = base_time * random.lognormvariate(0, 0.6)
    if status >= 500:
        request_time *= 5  # les erreurs serveur sont souvent lentes (timeouts)
    request_time = round(max(request_time, 0.001), 3)

    if status == 304:
        bytes_sent = 0
    elif status >= 400:
        bytes_sent = random.randint(100, 250)
    else:
        bytes_sent = random.randint(200, 6000)

    return {
        "time": ts.isoformat(timespec="seconds"),
        "remote_addr": ip,
        "method": method,
        "uri": uri,
        "status": status,
        "bytes_sent": bytes_sent,
        "request_time": request_time,
        "upstream_addr": random.choice(UPSTREAMS) if upstream else "",
        "upstream_status": str(status) if upstream else "",
        "upstream_response_time": f"{max(request_time - 0.001, 0.001):.3f}" if upstream else "",
        "user_agent": user_agent,
    }


def normal_entry(ts):
    method, template, _, statuses, base_time, upstream = random.choices(
        ROUTES, weights=[r[2] for r in ROUTES]
    )[0]
    ip = random.choices(CLIENT_IPS, weights=CLIENT_WEIGHTS)[0]
    user_agent = random.choices([u for u, _ in USER_AGENTS], weights=[w for _, w in USER_AGENTS])[0]
    return make_entry(ts, ip, method, fill_uri(template), weighted_choice(statuses), base_time, upstream, user_agent)


def attack_entry(ts, ip):
    return make_entry(ts, ip, "POST", "/login", 401, 0.060, True, ATTACK_USER_AGENT)


def spread(count, minutes):
    """Horodatages triés, répartis aléatoirement sur les `minutes` dernières minutes."""
    end = now()
    start = end - timedelta(minutes=minutes)
    total = (end - start).total_seconds()
    return sorted(start + timedelta(seconds=random.uniform(0, total)) for _ in range(count))


def write_lines(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" : pas de \r\n sous Windows, Logstash reçoit du JSON propre
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_batch(args):
    entries = [normal_entry(ts) for ts in spread(args.count, args.minutes)]
    write_lines(args.output, entries)
    print(f"{len(entries)} requêtes normales écrites dans {args.output} (sur {args.minutes} min)")


def run_attack(args):
    # Rafale de POST /login en 401 depuis une seule IP, noyée dans du trafic normal
    attack = [attack_entry(ts, args.attacker_ip) for ts in spread(args.attack_count, args.attack_minutes)]
    background = [normal_entry(ts) for ts in spread(args.attack_count // 3, args.attack_minutes)]
    entries = sorted(attack + background, key=lambda e: e["time"])
    write_lines(args.output, entries)
    print(
        f"Attaque simulée : {len(attack)} POST /login en 401 depuis {args.attacker_ip} "
        f"en {args.attack_minutes} min (+ {len(background)} requêtes normales) -> {args.output}"
    )


def run_follow(args):
    print(f"Trafic continu ({args.rate} req/s) vers {args.output}, Ctrl+C pour arrêter")
    try:
        while True:
            write_lines(args.output, [normal_entry(now())])
            time.sleep(random.expovariate(args.rate))  # arrivées aléatoires (processus de Poisson)
    except KeyboardInterrupt:
        print("\nArrêt du générateur")


def run_replay(args):
    """Recopie les logs du projet microservices-ha-cluster vers logs/ (Filebeat les lit ensuite)."""
    source = Path(args.source)
    if not source.is_file():
        sys.exit(f"Fichier source introuvable : {source} (option --source ou variable MICROSERVICES_LOGS)")
    output = args.output if args.output != DEFAULT_OUTPUT else DEFAULT_OUTPUT.with_name("microservices.log")

    def copy_from(offset):
        with source.open("r", encoding="utf-8") as f:
            f.seek(offset)
            lines = [line for line in f.read().splitlines() if line.strip()]
            offset = f.tell()
        if args.retime:
            # Remplace l'horodatage d'origine par l'heure actuelle pour voir les données
            # dans la fenêtre de temps par défaut de Kibana
            stamp = now().isoformat(timespec="seconds")
            write_lines(output, [{**json.loads(line), "time": stamp} for line in lines])
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("a", encoding="utf-8", newline="\n") as f:
                f.writelines(line + "\n" for line in lines)
        return offset, len(lines)

    offset, n = copy_from(0)
    print(f"{n} lignes copiées depuis {source} vers {output}")
    if not args.follow:
        return
    print("Suivi du fichier source (Ctrl+C pour arrêter)")
    try:
        while True:
            time.sleep(1)
            if source.stat().st_size < offset:  # fichier tronqué ou rotation : on repart du début
                offset = 0
            offset, n = copy_from(offset)
            if n:
                print(f"+{n} lignes")
    except KeyboardInterrupt:
        print("\nArrêt du suivi")


def parse_args():
    p = argparse.ArgumentParser(description="Génère des logs Nginx JSON pour la stack ELK.")
    p.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="fichier de sortie (défaut : logs/access.log)")
    p.add_argument("-n", "--count", type=int, default=500, help="nombre de requêtes normales (défaut : 500)")
    p.add_argument("--minutes", type=int, default=30, help="période couverte par les requêtes, en minutes (défaut : 30)")
    p.add_argument("--follow", action="store_true", help="écrire en continu (ou suivre le fichier source avec --source)")
    p.add_argument("--rate", type=float, default=5, help="requêtes par seconde en mode --follow (défaut : 5)")
    p.add_argument("--attack", action="store_true", help="simuler une attaque par force brute sur POST /login")
    p.add_argument("--attacker-ip", default=DEFAULT_ATTACKER_IP, help=f"IP de l'attaquant (défaut : {DEFAULT_ATTACKER_IP})")
    p.add_argument("--attack-count", type=int, default=300, help="nombre de tentatives échouées (défaut : 300)")
    p.add_argument("--attack-minutes", type=int, default=2, help="durée de l'attaque, en minutes (défaut : 2)")
    p.add_argument("--source", nargs="?", const=DEFAULT_SOURCE, default=None,
                   help="lire les logs de microservices-ha-cluster au lieu de générer "
                        "(défaut : $MICROSERVICES_LOGS ou ../microservices-ha-cluster/logs/access.log)")
    p.add_argument("--retime", action="store_true", help="avec --source : remplacer l'horodatage par l'heure actuelle")
    p.add_argument("--seed", type=int, help="graine aléatoire pour des résultats reproductibles")
    return p.parse_args()


def main():
    # Accents lisibles même quand la console Windows n'est pas en UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    if args.source is not None:
        run_replay(args)
    elif args.attack:
        run_attack(args)
    elif args.follow:
        run_follow(args)
    else:
        run_batch(args)


if __name__ == "__main__":
    main()
