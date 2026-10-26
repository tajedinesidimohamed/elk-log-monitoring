# elk-log-monitoring

> Documentation complète (architecture, composants, ce que j'ai appris) : en cours, phase 6.

Stack **ELK** (Elasticsearch, Logstash, Kibana) + **Filebeat** qui collecte des logs Nginx au format JSON,
les transforme, les indexe et les affiche dans un dashboard capable de faire ressortir une attaque par force brute.

## Lancement rapide

Prérequis : Docker Desktop (WSL2) avec au moins 4 Go de RAM alloués, Python 3, GNU Make.

```bash
make up         # lance la stack et importe le dashboard (~1 min 30 ; quelques minutes en plus au premier lancement pour télécharger les images)
make generate   # 500 requêtes normales sur les 30 dernières minutes
make attack     # attaque par force brute simulée
```

Dashboard : <http://localhost:5601/app/dashboards#/view/nginx-log-monitoring>

## Commandes

| Commande | Effet |
|---|---|
| `make up` | Lance la stack, attend que les 4 services soient sains et importe le dashboard |
| `make down` | Arrête la stack (les données Elasticsearch sont conservées) |
| `make generate` | Génère 500 requêtes normales réparties sur les 30 dernières minutes |
| `make traffic` | Génère du trafic normal en continu, 5 req/s (Ctrl+C pour arrêter) |
| `make attack` | Simule une attaque par force brute : 300 `POST /login` en 401 depuis `203.0.113.66` |
| `make replay` | Rejoue les logs Nginx du projet `microservices-ha-cluster` |
| `make dashboard` | Recrée la data view `nginx-*` et réimporte le dashboard |
| `make status` | État des conteneurs et des index `nginx-*` |
| `make logs` | Suit les logs des conteneurs |
| `make clean` | Supprime conteneurs, volumes et logs générés (remise à zéro) |

`make help` affiche cette liste. Le `Makefile` n'utilise que `docker compose` et `python` : il fonctionne
aussi bien dans PowerShell que dans Git Bash, WSL ou Linux.

## Scénario de démonstration

Objectif : montrer qu'on repère une attaque par force brute en quelques secondes, au milieu d'un trafic normal.

### 1. Trafic normal

```bash
make up
make generate
make traffic      # facultatif, dans un second terminal : trafic en direct
```

Ouvrir le dashboard (fenêtre « Last 1 hour », rafraîchi toutes les 10 s). Ce qu'on observe :

- un trafic régulier, très majoritairement en **2xx** (vert) ;
- quelques **4xx** isolés : scans de `/wp-admin` ou `/.env` (404), utilisateurs qui se trompent de mot de passe (401) ;
- le panneau **« Tentatives de connexion échouées par IP »** ne montre que 1 ou 2 échecs par IP : c'est le bruit normal.

<!-- Capture à ajouter : docs/images/01-trafic-normal.png -->

### 2. L'attaque

```bash
make attack
```

Le générateur écrit 300 `POST /login` refusés (401) depuis une seule IP, `203.0.113.66`, en 2 minutes,
avec le user-agent de l'outil de force brute THC-Hydra. Moins de 30 secondes plus tard, le dashboard change :

| Panneau | Signal |
|---|---|
| **Connexions échouées** (KPI rouge) | le compteur fait un bond d'environ +300 |
| **Requêtes dans le temps** | un pic de **4xx** (jaune) sur les 2 dernières minutes |
| **Répartition des codes HTTP** | la part de **401** explose |
| **Tentatives de connexion échouées par IP** | une barre rouge écrase toutes les autres : **203.0.113.66** |
| **Top 10 des IP** | l'attaquant passe en tête, devant les vrais clients |
| **Top des URL en erreur** | `/login` en 401 arrive en première ligne |

<!-- Capture à ajouter : docs/images/02-attaque-dashboard.png -->

### 3. L'enquête dans Kibana

1. **Filtrer sur l'attaquant** : cliquer sur la barre `203.0.113.66` dans le panneau des échecs de connexion.
   Le filtre s'applique à tout le dashboard : uniquement des `POST /login`, uniquement des 401.
2. **Lire les événements bruts** dans *Discover* (data view « Logs Nginx ») avec la requête KQL :
   ```
   remote_addr : "203.0.113.66"
   ```
   Ce qu'on y lit : user-agent `Mozilla/4.0 (Hydra)`, environ 150 requêtes par minute, toujours la même URL.
3. **Comparer avec le bruit normal** :
   ```
   tags : failed_login and not remote_addr : "203.0.113.66"
   ```
   Seulement quelques échecs isolés, répartis sur plusieurs IP et dans le temps.

**Conclusion** : beaucoup d'échecs d'authentification, une seule source, un délai très court et un client
automatisé. C'est la signature d'une attaque par force brute (MITRE ATT&CK
[T1110](https://attack.mitre.org/techniques/T1110/)).

<!-- Capture à ajouter : docs/images/03-enquete-discover.png -->

### 4. Et en production ?

La démo s'arrête à la détection. En production, on enchaînerait avec :

- **alerter** : une règle Kibana du type « plus de 20 échecs de connexion par IP en 1 minute » ;
- **bloquer** : `deny` Nginx, pare-feu ou `fail2ban` sur l'IP ;
- **prévenir** : limitation de débit sur `/login` (`limit_req` Nginx), verrouillage de compte, MFA.

### 5. Bonus : les logs du projet microservices-ha-cluster

```bash
make replay
```

Cette commande recopie `../microservices-ha-cluster/logs/access.log` (chemin réglable dans `.env` avec
`MICROSERVICES_LOGS`), en les horodatant à l'heure actuelle. Dans le dashboard, le filtre
`log_origin : "microservices-ha-cluster"` isole ces vrais logs du load balancer Nginx.

### Fin de la démo

```bash
make down    # arrêt, données conservées
make clean   # remise à zéro complète
```
