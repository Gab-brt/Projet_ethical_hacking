# TP Ethical Hacking — sockets TCP en Python

Un serveur Kali Linux et un client Debian (ou Windows) communiquent via une connexion TCP simple, **sans TLS et sans authentification**. Usage réservé à un laboratoire VM isolé.

## Prérequis

- Python 3 ; aucune bibliothèque externe.
- Deux VM sur le même réseau virtuel et capables de communiquer.

## 1. Trouver l'IP de Kali

```bash
ip -4 -br addr
```

Repérer l'IP de l'interface non-loopback (ex. `192.168.56.10`). **Ne pas utiliser `127.0.0.1`** : cette adresse désigne chaque VM elle-même.

## 2. Démarrer le serveur sur Kali

```bash
python3 server.py
```

Le serveur attend la connexion sur le port TCP `5000`.

## 3. Démarrer le client sur Debian

```bash
python3 client.py 192.168.56.10
```

Remplacer `192.168.56.10` par l'IP réelle de Kali.

## 4. Tester sur Kali

Depuis le prompt `serveur>`, taper :
- `ping` -> `pong`
- `hostname` -> nom de Debian
- `system` -> système d'exploitation du client
- `ip` -> IPv4 principale non-loopback du client
- `aide` -> commandes disponibles
- `quitter` -> fermeture propre de la session

Aucun shell distant arbitraire ni accès aux mots de passe n'est inclus : les opérations sont limitées aux diagnostics prédéfinis.

## Dépannage

- `Connection refused` : vérifier que le serveur tourne, son port et l'adresse de Kali.
- `timed out` : vérifier que les deux VM partagent un réseau accessible, et leurs pare-feu.
- `Address already in use` : un autre programme utilise le port 5000 ; arrêter l'ancien serveur.
- Tester depuis Debian : `ping -c 2 IP_KALI` et, si disponible, `nc -vz IP_KALI 5000`.
- Si les VM VirtualBox sont chacune sur **NAT** simple, elles peuvent ne pas pouvoir se joindre : configurer les deux avec le même **Réseau interne** ou **Réseau privé hôte** (ou un Réseau NAT partagé).

## Protocole

`socket.socket(AF_INET, SOCK_STREAM)` crée un socket TCP ; `bind` fixe le point d'écoute, `listen` attend les connexions, `accept` prend la connexion, `connect` établit la connexion, `sendall` envoie, et `readline` lit une commande ou réponse terminée par `\n`.

Ce protocole est du TCP en clair : ne pas l'utiliser sur Internet ou sur un réseau non fiable.
