"""Client TCP pédagogique - à lancer sur Debian ou Windows."""
import platform
import socket
import sys

PORT = 5000


def envoyer(sock, message):
    # Les sauts de ligne délimitent les messages dans le flux TCP.
    sock.sendall((message.replace("\n", " | ") + "\n").encode("utf-8"))


def adresse_ipv4():
    """Renvoie l'adresse de sortie principale, en excluant 127.0.0.1."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sonde:
            # connect() en UDP ne transmet rien : sélection de l'interface locale.
            sonde.connect(("192.0.2.1", 80))
            adresse = sonde.getsockname()[0]
            if not adresse.startswith("127."):
                return adresse
    except OSError:
        pass
    try:
        for adresse in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not adresse.startswith("127."):
                return adresse
    except OSError:
        pass
    return "Aucune adresse IPv4 non-loopback trouvée"


def traiter_commande(commande):
    if commande == "ping":
        return "pong"
    if commande == "hostname":
        return socket.gethostname()
    if commande == "system":
        return f"{platform.system()} {platform.release()}"
    if commande == "ip":
        return adresse_ipv4()
    if commande == "aide":
        return "Commandes : ping, hostname, system, ip, aide, quitter"
    if commande == "quitter":
        return "Au revoir"
    return "Commande non reconnue"


def main():
    if len(sys.argv) != 2:
        print("Usage : python3 client.py IP_DE_KALI")
        sys.exit(1)
    host = sys.argv[1]
    try:
        with socket.create_connection((host, PORT), timeout=10) as client:
            client.settimeout(None)
            print(f"[CONNECTÉ] Serveur {host}:{PORT}")
            with client.makefile("r", encoding="utf-8") as lecture:
                while True:
                    commande = lecture.readline()
                    if not commande:
                        print("[INFO] Le serveur a fermé la connexion.")
                        break
                    commande = commande.strip().lower()
                    print(f"[REÇU] {commande}")
                    envoyer(client, traiter_commande(commande))
                    if commande == "quitter":
                        break
    except (OSError, UnicodeError) as erreur:
        print(f"[ERREUR] Connexion impossible ou interrompue : {erreur}")
        sys.exit(1)


if __name__ == "__main__":
    main()
