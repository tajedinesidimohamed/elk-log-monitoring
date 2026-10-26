# Commandes du projet : fonctionnent dans PowerShell, Git Bash, WSL ou Linux.
# Les recettes n'utilisent que docker compose et python (aucune commande propre à un shell).

# Variables de .env (ports, chemins...) transmises aux scripts, si le fichier existe
-include .env
export

# "python" sous Windows, "python3" ailleurs ; surcharge possible : make generate PYTHON=py
ifeq ($(OS),Windows_NT)
PYTHON ?= python
else
PYTHON ?= python3
endif

.DEFAULT_GOAL := help
.PHONY: help up down generate traffic attack replay dashboard status logs clean

## Affiche cette aide
help:
	@$(PYTHON) -c "import re; [print(f'  make {m[1]:<10} {m[0]}') for m in re.findall(r'## (.+)\n(\w+):', open('Makefile', encoding='utf-8').read())]"

## Lance la stack, attend qu'elle soit prête et importe le dashboard
up:
	docker compose up -d --wait
	$(PYTHON) kibana/setup_dashboard.py

## Arrête la stack (les données Elasticsearch sont conservées)
down:
	docker compose down

## Génère 500 requêtes normales réparties sur les 30 dernières minutes
generate:
	$(PYTHON) generator/generate_logs.py

## Génère du trafic normal en continu (Ctrl+C pour arrêter)
traffic:
	$(PYTHON) generator/generate_logs.py --follow --rate 5

## Simule une attaque par force brute sur POST /login
attack:
	$(PYTHON) generator/generate_logs.py --attack

## Rejoue les logs Nginx du projet microservices-ha-cluster
replay:
	$(PYTHON) generator/generate_logs.py --source --retime

## Recrée la data view nginx-* et réimporte le dashboard
dashboard:
	$(PYTHON) kibana/setup_dashboard.py

## Affiche l'état des conteneurs et des index nginx-*
status:
	docker compose ps
	curl -s "http://localhost:$(or $(ES_PORT),9200)/_cat/indices/nginx-*?v&s=index"

## Suit les logs des conteneurs (Ctrl+C pour arrêter)
logs:
	docker compose logs -f --tail=50

## Supprime conteneurs, volumes (données ES, registre Filebeat) et logs générés
clean:
	docker compose down -v
	$(PYTHON) -c "import pathlib; [p.unlink() for p in pathlib.Path('logs').glob('*.log')]"
