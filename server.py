#!/usr/bin/env python3
"""Serveur TCP de commandes + dashboard MJPEG local, sans WebRTC/aiortc."""

import argparse
import json
import logging
import os
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
MAX_CLIENTS = 100
MAX_FRAME = 8 * 1024 * 1024
LOG_FILE = BASE_DIR / "server.log"
WEB_LOG_FILE = BASE_DIR / "server_web.log"
COMMANDES = {"ping", "hostname", "system", "ip", "aide", "video", "stopvideo", "quitter"}
TOKEN = os.environ.get("SCREEN_MONITOR_TOKEN", "").strip()

logger = logging.getLogger("tcp")
logger.setLevel(logging.INFO)
logger.addHandler(RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
logger.addHandler(logging.StreamHandler())
web_logger = logging.getLogger("webvideo")
web_logger.setLevel(logging.INFO)
web_logger.addHandler(RotatingFileHandler(WEB_LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8"))
web_logger.propagate = False

arret = threading.Event()
verrou_clients = threading.Lock()
verrou_flux = threading.Lock()
clients = []
flux = {}  # client_id -> {frame, updated, active}


DASHBOARD = r'''
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Supervision — Flux vidéo</title>

<style>
body {
    margin:0;
    padding:24px;
    background:#10151d;
    color:#e8edf5;
    font:15px system-ui;
}
header {
    display:flex;
    justify-content:space-between;
    align-items:center;
}
h1 {font-size:1.35rem}
.grid {
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
    gap:16px;
}
.card {
    background:#19222e;
    border:1px solid #394657;
    border-radius:12px;
    overflow:hidden;
}
.card h2 {
    font-size:1rem;
    padding:10px 14px;
    margin:0;
}
img {
    width:100%;
    aspect-ratio:16/9;
    object-fit:contain;
    background:#05080c;
}
.note {
    color:#aab8c9;
    line-height:1.5;
}
button {
    padding:10px 14px;
    border:0;
    border-radius:8px;
    color:white;
    cursor:pointer;
    font-weight:600;
}
.actions {
    display:flex;
    gap:8px;
    padding:12px;
}
.actions button {flex:1}
.screen {background:#245b91}

/* Couleurs inversées */
.record {background:#23834b}
.record.on {background:#a83232}

.refresh {background:#245b91}
.record-status {
    padding:0 12px 12px;
    color:#aab8c9;
    font-size:13px;
}
button:disabled {
    opacity:.5;
    cursor:not-allowed;
}
</style>
</head>

<body>

<header>
    <h1>Supervision — flux vidéo en direct</h1>
    <span id="status">Connexion...</span>
</header>

<p class="note">
    Les flux apparaissent uniquement lorsqu'un poste autorisé
    reçoit la commande video.
    Les captures et enregistrements sont déclenchés manuellement.
</p>

<button class="refresh" onclick="refresh()">Actualiser</button>

<div id="streams" class="grid"></div>

<script>

const recorders = new Map();

function safeName(value) {
    return value.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function findCard(id) {
    return [...document.querySelectorAll(".card")]
        .find(c => c.dataset.clientId === id);
}

function makeCanvas(img) {
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;

    const ctx = canvas.getContext("2d");

    return {canvas, ctx};
}

function drawFrame(img, ctx, canvas) {
    if (img.complete && img.naturalWidth > 0) {
        try {
            ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        } catch(e) {
            console.error("Capture image :", e);
        }
    }
}


// Téléchargement local dans le navigateur
function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = url;
    link.download = filename;

    document.body.appendChild(link);
    link.click();
    link.remove();

    setTimeout(() => URL.revokeObjectURL(url), 30000);
}


// SCREEN : capture PNG
function screen(id) {
    const card = findCard(id);
    const img = card?.querySelector("img");

    if (!img || !img.naturalWidth) {
        alert("Aucune image disponible.");
        return;
    }

    const {canvas, ctx} = makeCanvas(img);
    drawFrame(img, ctx, canvas);

    canvas.toBlob(blob => {
        if (!blob) {
            alert("Impossible de créer la capture.");
            return;
        }

        downloadBlob(
            blob,
            "SCREEN_" + safeName(id) + "_" +
            Date.now() + ".png"
        );
    }, "image/png");
}


// RECORD ON / OFF
function record(id) {
    if (recorders.has(id)) {
        stopRecord(id);
    } else {
        startRecord(id);
    }
}


// Démarrage de l'enregistrement
async function startRecord(id) {

    const card = findCard(id);
    const img = card?.querySelector("img");

    if (!img || !img.naturalWidth) {
        alert("Aucun flux vidéo disponible.");
        return;
    }

    if (!window.MediaRecorder ||
        !HTMLCanvasElement.prototype.captureStream) {
        alert("Enregistrement non supporté par ce navigateur.");
        return;
    }

    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;

    const ctx = canvas.getContext("2d");
    const stream = canvas.captureStream(15);

    const mimeType = [
        "video/webm;codecs=vp9",
        "video/webm;codecs=vp8",
        "video/webm"
    ].find(type => MediaRecorder.isTypeSupported(type));

    if (!mimeType) {
        stream.getTracks().forEach(t => t.stop());
        alert("Aucun format vidéo compatible.");
        return;
    }

    const chunks = [];
    const controller = new AbortController();

    let recorder;

    try {
        recorder = new MediaRecorder(stream, {mimeType});
    } catch(e) {
        stream.getTracks().forEach(t => t.stop());
        alert(e.message);
        return;
    }

    const state = {
        recorder,
        stream,
        controller,
        chunks,
        running: true
    };

    recorders.set(id, state);
    updateRecordButton(id);

    recorder.ondataavailable = event => {
        if (event.data && event.data.size > 0) {
            chunks.push(event.data);
        }
    };

    recorder.onerror = event => {
        console.error("Erreur MediaRecorder :", event);
    };

    recorder.onstop = () => {
        const blob = new Blob(chunks, {type:mimeType});

        if (blob.size > 0) {
            downloadBlob(
                blob,
                "RECORD_" + safeName(id) + "_" +
                Date.now() + ".webm"
            );
        } else {
            alert("L'enregistrement ne contient aucune donnée.");
        }

        stream.getTracks().forEach(t => t.stop());

        recorders.delete(id);
        updateRecordButton(id);
    };

    recorder.start(1000);

    // Lecture directe du flux MJPEG
    try {
        const response = await fetch(
            "/stream/" + encodeURIComponent(id),
            {
                cache: "no-store",
                signal: controller.signal
            }
        );

        if (!response.ok || !response.body) {
            throw new Error("Impossible de lire le flux vidéo.");
        }

        const reader = response.body.getReader();
        let buffer = new Uint8Array(0);

        while (state.running) {

            const {value, done} = await reader.read();

            if (done) {
                break;
            }

            const combined = new Uint8Array(
                buffer.length + value.length
            );

            combined.set(buffer);
            combined.set(value, buffer.length);
            buffer = combined;

            // Recherche des images JPEG dans le flux MJPEG
            while (true) {

                let start = -1;
                let end = -1;

                // Début JPEG : FF D8
                for (let i = 0; i < buffer.length - 1; i++) {
                    if (
                        buffer[i] === 0xFF &&
                        buffer[i + 1] === 0xD8
                    ) {
                        start = i;
                        break;
                    }
                }

                if (start < 0) {
                    buffer = buffer.slice(-1);
                    break;
                }

                // Fin JPEG : FF D9
                for (
                    let i = start + 2;
                    i < buffer.length - 1;
                    i++
                ) {
                    if (
                        buffer[i] === 0xFF &&
                        buffer[i + 1] === 0xD9
                    ) {
                        end = i + 2;
                        break;
                    }
                }

                if (end < 0) {
                    buffer = buffer.slice(start);
                    break;
                }

                const jpeg = buffer.slice(start, end);
                buffer = buffer.slice(end);

                try {
                    const bitmap = await createImageBitmap(
                        new Blob([jpeg], {
                            type:"image/jpeg"
                        })
                    );

                    if (state.running) {
                        ctx.drawImage(
                            bitmap,
                            0,
                            0,
                            canvas.width,
                            canvas.height
                        );
                    }

                    bitmap.close();

                } catch(e) {
                    console.warn("Image JPEG ignorée :", e);
                }
            }

            // Protection contre une accumulation excessive
            if (buffer.length > 10 * 1024 * 1024) {
                buffer = new Uint8Array(0);
            }
        }

        if (state.running) {
            stopRecord(id);
        }

    } catch(e) {

        if (e.name !== "AbortError") {
            console.error("Erreur flux enregistrement :", e);

            if (state.running) {
                alert("Erreur pendant la lecture du flux : " + e.message);
                stopRecord(id);
            }
        }
    }
}


// Arrêt de l'enregistrement
function stopRecord(id) {

    const state = recorders.get(id);

    if (!state) return;

    state.running = false;
    state.controller.abort();

    if (state.recorder.state !== "inactive") {
        state.recorder.stop();
    }
}


// Mise à jour du bouton RECORD
function updateRecordButton(id) {

    const card = findCard(id);
    if (!card) return;

    const active = recorders.has(id);

    const button = card.querySelector(".record");
    const status = card.querySelector(".record-status");

    if (!button || !status) return;

    button.textContent = active ? "RECORD ON" : "RECORD OFF";
    button.classList.toggle("on", active);

    status.textContent = active
        ? "● Enregistrement en cours"
        : "Enregistrement arrêté";
}


// Création d'une carte
function createCard(id) {

    const card = document.createElement("section");
    card.className = "card";
    card.dataset.clientId = id;

    const title = document.createElement("h2");
    title.className = "stream-title";

    const img = document.createElement("img");
    img.alt = "Flux de " + id;
    img.src = "/stream/" + encodeURIComponent(id);

    const actions = document.createElement("div");
    actions.className = "actions";

    const screenButton = document.createElement("button");
    screenButton.className = "screen";
    screenButton.textContent = "SCREEN";
    screenButton.onclick = () => screen(id);

    const recordButton = document.createElement("button");
    recordButton.className = "record";
    recordButton.textContent = "RECORD OFF";
    recordButton.onclick = () => record(id);

    const status = document.createElement("div");
    status.className = "record-status";
    status.textContent = "Enregistrement arrêté";

    actions.append(screenButton, recordButton);
    card.append(title, img, actions, status);

    return card;
}


// Actualisation des flux
async function refresh() {

    try {

        const response = await fetch("/api/streams", {
            cache:"no-store"
        });

        if (!response.ok) {
            throw new Error("HTTP " + response.status);
        }

        const streams = await response.json();
        const root = document.getElementById("streams");

        const ids = new Set(
            streams.map(x => x.client_id)
        );

        for (const card of [...root.children]) {
            if (!ids.has(card.dataset.clientId)) {
                card.remove();
            }
        }

        for (const x of streams) {

            let card = findCard(x.client_id);

            if (!card) {
                card = createCard(x.client_id);
                root.append(card);
            }

            card.querySelector(".stream-title").textContent =
                x.client_id +
                (x.active ? " — actif" : " — arrêté");

            updateRecordButton(x.client_id);
        }

        document.getElementById("status").textContent =
            streams.length + " poste(s)";

        if (!streams.length) {
            root.textContent = "Aucun flux vidéo actif.";
        }

    } catch(e) {

        document.getElementById("status").textContent =
            "Erreur : " + e.message;
    }
}


refresh();
setInterval(refresh, 2000);

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


def recevoir_video(connexion, adresse):
    client_id = "inconnu"
    try:
        connexion.settimeout(15)
        # Lire l'en-tête octet par octet : makefile.readline() peut pré-lire
        # les premiers octets JPEG et les retirer du flux utilisé ensuite par recv().
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


def envoyer(connexion, message):
    connexion.sendall((message + "\n").encode("utf-8"))


def supprimer_client(client):
    connexion, lecture, adresse = client
    with verrou_clients:
        if client not in clients: return
        clients.remove(client)
    try: lecture.close()
    except OSError: pass
    try: connexion.shutdown(socket.SHUT_RDWR)
    except OSError: pass
    try: connexion.close()
    except OSError: pass
    logger.info("Client TCP déconnecté : %s:%s", adresse[0], adresse[1])


def accepter_clients(serveur):
    while not arret.is_set():
        try: connexion, adresse = serveur.accept()
        except socket.timeout: continue
        except OSError: break
        with verrou_clients:
            if len(clients) >= MAX_CLIENTS:
                client = None
            else:
                try:
                    lecture = connexion.makefile("r", encoding="utf-8", newline="\n")
                    client = (connexion, lecture, adresse)
                    clients.append(client)
                except OSError: client = None
        if client is None: connexion.close()
        else: logger.info("Nouveau client TCP : %s:%s", adresse[0], adresse[1])


def console_admin():
    print("Commandes : ping, hostname, system, ip, aide, video, stopvideo, quitter")
    while not arret.is_set():
        try: commande = input("serveur> ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            arret.set(); break
        if not commande: continue
        if commande not in COMMANDES:
            print("[ERREUR] Commande inconnue."); continue
        with verrou_clients: actuels = list(clients)
        if not actuels:
            print("[INFO] Aucun client TCP connecté.")
            if commande == "quitter": arret.set(); break
            continue
        for client in actuels:
            connexion, lecture, adresse = client
            try:
                envoyer(connexion, commande)
                reponse = lecture.readline()
                if not reponse: supprimer_client(client)
                else: print(f"Client {adresse[0]}:{adresse[1]} : {reponse.rstrip()}")
            except (OSError, UnicodeError, ValueError) as erreur:
                print(f"[ERREUR] {adresse} : {erreur}"); supprimer_client(client)
        if commande == "quitter": arret.set(); break


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
    """Ouvre un terminal séparé qui suit exclusivement server_web.log."""
    log = str(WEB_LOG_FILE.resolve())
    try:
        if os.name == "nt":
            # Force une vraie deuxième fenêtre Windows, indépendante du terminal principal.
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
    web = None
    try:
        tcp.bind((TCP_HOST, TCP_PORT)); tcp.listen(MAX_CLIENTS)
        video.bind((VIDEO_HOST, VIDEO_PORT)); video.listen(MAX_CLIENTS)
        web = demarrer_web()
        threading.Thread(target=accepter_clients, args=(tcp,), daemon=True).start()
        threading.Thread(target=accepter_video, args=(video,), daemon=True).start()
        ouvrir_terminal_logs()
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")).start()
        print("\n" + "=" * 58)
        print("             SERVEUR MONITORING DÉMARRÉ")
        print("=" * 58)
        print(f"[TCP COMMANDES] {TCP_HOST}:{TCP_PORT}")
        print(f"[TCP VIDÉO]     {TCP_HOST}:{VIDEO_PORT}")
        print(f"[DASHBOARD]     http://127.0.0.1:{WEB_PORT}")
        print(f"[LOGS WEB]      {WEB_LOG_FILE}")
        print("[INFO] Le partage démarre uniquement avec la commande video.")
        print("[INFO] CTRL+C pour arrêter.")
        print("=" * 58 + "\n")
        threading.Thread(target=console_admin, daemon=True).start()
        while not arret.wait(0.25): pass
    finally:
        arret.set()
        if web: web.shutdown(); web.server_close()
        tcp.close(); video.close()
        with verrou_clients: actuels = list(clients)
        for c in actuels: supprimer_client(c)
        logger.info("Serveur arrêté.")


if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: arret.set()
    except OSError as erreur:
        logger.exception("Impossible de démarrer le serveur : %s", erreur)
        raise SystemExit(1)
