"""Serveur TCP pédagogique - à lancer sur Kali Linux."""
import socket

HOST = "0.0.0.0"  # Écoute sur les interfaces réseau de Kali
PORT = 5000
COMMANDES = {"ping", "hostname", "system", "ip", "aide", "quitter"}


def envoyer(sock, message):
    sock.sendall((message + "\n").encode("utf-8"))


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as serveur:
        # Facilite le redémarrage après un arrêt du script.
        serveur.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        serveur.bind((HOST, PORT))
        serveur.listen(1)
        print(f"[SERVEUR] En écoute sur le port {PORT}...")
        print("[SERVEUR] Une connexion client est attendue.")

        connexion, adresse = serveur.accept()
        with connexion:
            print(f"[CONNECTÉ] Client : {adresse[0]}:{adresse[1]}")
            with connexion.makefile("r", encoding="utf-8") as lecture:
                print("Commandes : ping, hostname, system, ip, aide, quitter")
                while True:
                    try:
                        commande = input("serveur> ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        commande = "quitter"

                    if not commande:
                        continue
                    if commande not in COMMANDES:
                        print("[ERREUR] Commande inconnue.")
                        continue
                    try:
                        envoyer(connexion, commande)
                        reponse = lecture.readline()
                    except (OSError, UnicodeError) as erreur:
                        print(f"[ERREUR] Connexion perdue : {erreur}")
                        break
                    if not reponse:
                        print("[INFO] Le client a fermé la connexion.")
                        break
                    print("Client :", reponse.rstrip("\n"))
                    if commande == "quitter":
                        break


if __name__ == "__main__":
    try:
        main()
    except OSError as erreur:
        print(f"[ERREUR] Impossible de démarrer le serveur : {erreur}")
