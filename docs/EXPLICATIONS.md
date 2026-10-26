# Explications : les notions clés de la stack ELK

Ce document explique les notions utilisées dans le projet, avec des exemples tirés du projet lui-même.
Il se termine par 10 questions d'entretien avec des réponses courtes.

## Sommaire

- [Document](#document)
- [Index](#index)
- [Mapping](#mapping)
- [Pipeline Logstash](#pipeline-logstash)
- [Rôle de Filebeat](#rôle-de-filebeat)
- [Logs et métriques](#logs-et-métriques)
- [10 questions d'entretien](#10-questions-dentretien)

---

## Document

Un **document** est l'unité de base stockée dans Elasticsearch : un objet **JSON**. Ici, **une requête HTTP
= un document**.

Une ligne de log Nginx devient, après passage dans Logstash, le document suivant (extrait) :

```json
{
  "@timestamp": "2026-10-26T15:20:21.000Z",
  "remote_addr": "203.0.113.66",
  "method": "POST",
  "uri": "/login",
  "status": 401,
  "status_family": "4xx",
  "response_time_ms": 72,
  "user_agent": "Mozilla/4.0 (Hydra)",
  "tags": ["failed_login"]
}
```

Chaque document reçoit un identifiant unique (`_id`) et appartient à un index.

> Analogie avec une base relationnelle : un document ressemble à une **ligne** d'une table, mais il n'y a
> pas de schéma imposé à l'avance, et il peut contenir des objets imbriqués (`ua.os.name`).

## Index

Un **index** est une collection de documents du même genre : l'équivalent d'une **table**. C'est la cible des
recherches : `GET nginx-*/_search` interroge tous les index dont le nom commence par `nginx-`.

Dans ce projet, Logstash crée **un index par jour** : `nginx-2026.10.26`, `nginx-2026.10.27`… C'est l'usage
classique pour des logs :

- supprimer les vieux logs revient à **supprimer un index entier**, ce qui est instantané (bien plus rapide
  que d'effacer des documents un par un) ;
- les recherches sur une période récente ne touchent que les index concernés.

Un index est découpé en **shards** (morceaux répartis sur les nœuds du cluster). Chaque shard peut avoir des
**réplicas** (copies sur d'autres nœuds) pour la tolérance aux pannes. Avec un seul nœud, un réplica ne peut
pas être placé à côté de son original. C'est pour ça que le template fixe `number_of_replicas: 0` : l'index
est alors « green » au lieu de « yellow ».

Une **data view** Kibana (`nginx-*`) indique à Kibana quels index lire et quel champ porte le temps
(`@timestamp`).

## Mapping

Le **mapping** est le **schéma** d'un index : le type de chaque champ. Il détermine ce qu'on peut faire avec
le champ :

| Type | Exemple du projet | Ce qu'il permet |
|---|---|---|
| `keyword` | `uri`, `method`, `status_family` | Valeur exacte : filtres, top 10, camemberts |
| `text` | `message` | Recherche plein texte (découpage en mots) |
| `short`, `integer`, `float` | `status`, `response_time_ms` | Comparaisons (`status >= 400`), moyennes, percentiles |
| `ip` | `remote_addr` | Recherche par plage (`192.0.2.0/24`) |
| `date` | `@timestamp` | Axe du temps, filtres par période |

Sans mapping explicite, Elasticsearch **devine** les types à partir du premier document reçu (*dynamic
mapping*). Une IP devient alors du texte, et un nombre envoyé entre guillemets reste une chaîne. Or **on ne
peut pas changer le type d'un champ existant** : il faut créer un nouvel index et y recopier les données
(*reindex*).

C'est pour ça que le projet fournit un **index template**
([logstash/templates/nginx-template.json](../logstash/templates/nginx-template.json)). Il est appliqué
automatiquement à chaque nouvel index `nginx-*`.

## Pipeline Logstash

Un **pipeline** Logstash est une chaîne de traitement en trois étapes :

```
input  ──►  filter  ──►  output
(recevoir)  (transformer)  (envoyer)
```

Dans [logstash/pipeline/nginx.conf](../logstash/pipeline/nginx.conf) :

1. **input** `beats` : reçoit les lignes envoyées par Filebeat sur le port 5044.
2. **filter** : transforme chaque événement.
   - `json` (ou `grok` pour du texte) : découpe la ligne en champs ;
   - `date` : utilise l'heure de la requête comme `@timestamp` ;
   - `mutate` : convertit les types et supprime les champs inutiles ;
   - conditions `if` : calculent `status_family` et posent le tag `failed_login` ;
   - `useragent` : extrait le navigateur et le système.
3. **output** `elasticsearch` : envoie le document dans l'index du jour.

`grok` mérite une mention : il découpe un texte libre à l'aide de motifs nommés, par exemple
`%{IPORHOST:remote_addr}` ou `%{NUMBER:status}`. C'est l'outil de base pour les logs qui ne sont pas en JSON.
Écrire les logs directement en JSON, comme le fait le Nginx de `microservices-ha-cluster`, évite ce travail
et les erreurs de parsing.

## Rôle de Filebeat

**Filebeat** est un agent léger (écrit en Go, environ 130 Mo ici) installé là où sont les logs. Son seul
travail : **lire les fichiers et envoyer les nouvelles lignes**, de façon fiable.

- Il **surveille** des fichiers (`logs/*.log`) et détecte les nouvelles lignes et les nouveaux fichiers.
- Il **retient sa position** dans chaque fichier, dans un *registre*. Après un redémarrage, il reprend là où
  il s'était arrêté.
- Il garantit une livraison **au moins une fois** (*at-least-once*) : une ligne n'est considérée comme
  envoyée qu'une fois confirmée par Logstash.
- Il gère la **contre-pression** (*backpressure*) : si Logstash est saturé, Filebeat ralentit au lieu de
  perdre des données.

**Pourquoi Filebeat *et* Logstash ?** Logstash est puissant mais lourd (une JVM). On ne l'installe pas sur
chaque serveur. Filebeat collecte sur chaque machine, et un Logstash central fait les transformations.

## Logs et métriques

| | **Logs** | **Métriques** |
|---|---|---|
| Nature | Un **événement** daté, avec son contexte | Une **valeur numérique** mesurée à intervalle régulier |
| Exemple | `POST /login 401 depuis 203.0.113.66 à 15:20:21` | « 42 requêtes/s », « CPU à 73 % » |
| Volume | Élevé (une ligne par requête) | Faible et prévisible (un point toutes les N secondes) |
| Question typique | « **Pourquoi** et **qui** ? » | « **Combien** et **est-ce normal** ? » |
| Outils | ELK, Loki | Prometheus, Grafana, Metricbeat |

Les deux se complètent. Une métrique déclenche l'alerte (« le taux de 401 explose ») et les logs expliquent
la cause (« une seule IP, user-agent Hydra »). Avec les **traces** (le parcours d'une requête entre les
services), ce sont les **trois piliers de l'observabilité**.

Dans ce projet, on **dérive des métriques à partir des logs** : le nombre de requêtes par minute ou le temps
de réponse moyen sont des agrégations calculées par Elasticsearch sur les documents.

---

## 10 questions d'entretien

**1. Quel est le rôle de chaque composant de la stack ELK ?**
Filebeat collecte les logs sur les machines. Logstash les parse, les type et les enrichit. Elasticsearch les
stocke et les indexe pour la recherche et les agrégations. Kibana permet de les explorer et de les visualiser.

**2. Pourquoi utiliser Filebeat en plus de Logstash ?**
Filebeat est très léger : on peut l'installer sur chaque serveur. Il garantit la reprise après un redémarrage
(registre) et gère la contre-pression. Logstash, plus lourd (JVM), est centralisé et fait les transformations
complexes.

**3. Qu'est-ce qu'un shard et un réplica ? Pourquoi un index peut-il être « yellow » ?**
Un shard est un morceau d'index, réparti sur les nœuds. Un réplica est une copie d'un shard sur un autre
nœud, pour la tolérance aux pannes et la lecture. « Yellow » veut dire que les shards primaires sont en
place, mais pas tous les réplicas. C'est le cas typique d'un cluster à un nœud qui a 1 réplica configuré.

**4. Qu'est-ce qu'un mapping, et pourquoi définir un index template ?**
Le mapping est le schéma : le type de chaque champ. Sans template, Elasticsearch devine les types et peut se
tromper (une IP stockée en texte). Un type ne peut pas être modifié sur un index existant, il faut réindexer.
Le template applique le bon mapping à chaque nouvel index.

**5. Quelle est la différence entre `text` et `keyword` ?**
`text` est découpé en mots (analysé) : il sert à la recherche plein texte. `keyword` est stocké tel quel : il
sert aux filtres exacts, aux tris et aux agrégations (top 10). Par exemple, une URL ou un code pays se met en
`keyword`, un message d'erreur en `text`.

**6. Pourquoi `@timestamp` doit-il venir du log plutôt que de l'heure d'ingestion ?**
Parce que les logs peuvent arriver en retard : panne réseau, rejeu, file d'attente. Avec l'heure
d'ingestion, les événements seraient mal placés dans le temps, et la chronologie d'un incident serait
fausse. On utilise le filtre `date` de Logstash.

**7. Comment gérer la rétention des logs ?**
Avec des index par période (journaliers) et une politique ILM (Index Lifecycle Management). Elle fait passer
les index par des phases hot → warm → cold, puis les supprime après N jours. Supprimer un index entier est
instantané. La durée de rétention dépend du coût et des obligations légales.

**8. Comment détecter une attaque par force brute dans des logs web ?**
On compte les échecs d'authentification (401/403 sur la page de login) **par IP** et **par fenêtre de temps**.
Un volume anormal venant d'une seule source, en peu de temps, avec un user-agent d'outil automatisé, signe
l'attaque (MITRE ATT&CK T1110). Une variante plus discrète, le *password spraying*, se repère plutôt en
comptant les comptes visés par IP.

**9. Comment sécuriser une stack ELK en production ?**
- activer X-Pack Security : authentification et rôles au moindre privilège ;
- TLS sur toutes les liaisons (Beats → Logstash → Elasticsearch → Kibana) ;
- secrets hors du dépôt (variables d'environnement, coffre-fort de secrets) ;
- ne jamais exposer Elasticsearch sur Internet, Kibana derrière un reverse proxy HTTPS ;
- journaux d'audit activés.

**10. Quelle différence entre logs, métriques et traces ? Quand utiliser ELK ou Prometheus ?**
Les logs sont des événements détaillés, les métriques des valeurs numériques régulières, les traces le
parcours d'une requête entre les services. Prometheus et Grafana excellent pour les métriques et les alertes
sur des seuils, à faible coût. ELK excelle pour chercher et enquêter dans des événements détaillés
(dépannage, sécurité, SOC). En pratique, on utilise souvent les deux.
