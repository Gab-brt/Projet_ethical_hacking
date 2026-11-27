Monitoring TCP — dossier leo+
Cette partie du dépôt fournit une démonstration locale de supervision réseau composée de deux programmes Python :

server.py : serveur de commandes, serveur vidéo MJPEG et dashboard web.
client.py : agent installé sur une machine autorisée, capable de répondre aux commandes et de diffuser son écran.
[!WARNING] Utilisez ces programmes uniquement dans un laboratoire isolé et sur des machines dont vous avez l'autorisation explicite. Ils peuvent permettre l'exécution de commandes avec des privilèges élevés, la consultation d'informations système et la capture d'écran. N'utilisez pas cette démonstration sur des machines ou réseaux de production sans accord préalable.

Fonctionnement
Le client établit une connexion TCP persistante vers le serveur. Plusieurs services distincts peuvent fonctionner simultanément :

Service	Adresse par défaut	Usage
Commandes TCP	0.0.0.0:5000	Échange des commandes et réponses
Flux vidéo	0.0.0.0:5001	Capture et transmission JPEG
Shell local	127.0.0.1:5002	Communication interne du terminal
Dashboard web	http://127.0.0.1:8080	Visualisation des flux MJPEG
Le serveur accepte jusqu'à 100 clients et conserve leurs informations en mémoire. Les journaux sont écrits dans server.log et server_web.log, à côté de server.py.

Prérequis
Serveur
Python 3
Un navigateur web
Un réseau privé ou VirtualBox « Host-Only »
Sur Linux ou Windows, lancez le serveur depuis ce dossier :

python3 server.py

Le serveur ouvre automatiquement le dashboard local et affiche la console d'administration.

Client
Python 3
Linux ou Windows
Sur Linux : python3-venv, sudo et les bibliothèques nécessaires à l'installation automatique
Pour la capture d'écran : les modules Python mss et Pillow
Sur Linux, le client crée un environnement virtuel et installe mss puis Pillow si nécessaire. Sur Windows, ces dépendances doivent être présentes avant l'installation.

Installation et lancement
Sur le serveur, démarrez d'abord server.py :

python3 server.py

Sur la machine cible autorisée, installez puis démarrez le client avec l'adresse IPv4 du serveur :

python3 client.py 192.168.56.10

Cette commande vérifie que le port TCP 5000 est accessible, mémorise l'adresse du serveur et configure le démarrage automatique :

Linux : service utilisateur clienttcp.service via systemd.
Windows : tâche planifiée ClientTCP via schtasks.
Pour lancer manuellement le client déjà configuré :

python3 client.py --client

Pour désinstaller uniquement les composants de démarrage automatique :

python3 client.py --uninstall

Console du serveur
Depuis la console interactive, les principales commandes sont :

Commande	Description
inventaire	Liste les clients actuellement connectés
ping	Vérifie la disponibilité des cibles
hostname	Affiche le nom des machines
system	Affiche le système d'exploitation
ip	Affiche les adresses IPv4 détectées
video	Lance la capture d'écran
stopvideo	Arrête la capture d'écran
exec <commande>	Exécute une commande via le canal shell
shell	Ouvre le terminal interactif
aide	Affiche l'aide
quitter	Arrête le serveur
Lorsque plusieurs clients sont connectés, le serveur demande généralement de choisir la cible avant d'envoyer une commande.

Dashboard vidéo
Après le démarrage de server.py, ouvrez :

http://127.0.0.1:8080

Dans l'interface, chaque client ayant démarré sa capture apparaît avec un identifiant basé sur son nom d'hôte et son UUID local. Les flux sont accessibles sous /stream/<client_id>.

Fichiers créés sur la machine cliente
Selon le système, le client peut créer :

Élément	Emplacement
Configuration	~/.config/clienttcp/ sous Linux ou %APPDATA%\clienttcp\ sous Windows
Adresse du serveur	server_ip
UUID client	client_uuid
Journal vidéo	video_error.log
PID de l'instance	client.pid
Service Linux	~/.config/systemd/user/clienttcp.service
Tâche Windows	ClientTCP
Sous Linux, le premier lancement peut demander les droits administrateur afin de créer une règle sudo limitée à /bin/bash pour la commande shell persistante.

Dépannage
Le client ne se connecte pas
Vérifiez que le serveur écoute sur 192.168.56.10.
Autorisez temporairement les ports 5000 et 5001 dans le pare-feu du laboratoire.
Vérifiez que les machines peuvent communiquer avec ping.
Relancez le client avec python3 client.py --client.
Aucun flux vidéo n'apparaît
Lancez video depuis la console du serveur.
Vérifiez que mss et Pillow sont installés sur la cible.
Consultez ~/.config/clienttcp/video_error.log sur Linux ou le répertoire équivalent dans %APPDATA%.
Vérifiez que la session graphique permet la capture d'écran.
Plusieurs instances démarrent
Le client conserve un PID dans client.pid et tente d'arrêter une instance précédente lorsqu'elle est encore active.

Sécurité et bonnes pratiques
Gardez le serveur lié à 127.0.0.1 pour le web et le shell local.
Utilisez exclusivement un réseau virtuel isolé pour les tests.
Obtenez une autorisation écrite avant toute capture d'écran.
Évitez de placer des données personnelles ou des identifiants dans les machines du laboratoire.
Consultez les journaux server.log et server_web.log après chaque démonstration.
