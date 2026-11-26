"""Serveur TCP pédagogique acceptant jusqu'à 100 clients."""
import socket
import threading

HOST = "0.0.0.0"
PORT = 5000
MAX_CLIENTS = 100
COMMANDES = {"ping", "hostname", "system", "ip", "aide", "quitter"}


def envoyer(sock, message):
    sock.sendall((message + "\n").encode("utf-8"))


def supprimer_client(client, clients, verrou):
    with verrou:
        if client not in clients:
            return
        clients.remove(client)

    connexion, lecture, adresse = client
    lecture.close()
    connexion.close()
    print(f"[DÉCONNECTÉ] {adresse[0]}:{adresse[1]}")


def accepter_clients(serveur, clients, verrou):
    while True:
        try:
            connexion, adresse = serveur.accept()
            lecture = connexion.makefile("r", encoding="utf-8")
            client = (connexion, lecture, adresse)

            with verrou:
                if len(clients) >= MAX_CLIENTS:
                    envoyer(connexion, "Serveur complet")
                    lecture.close()
                    connexion.close()
                    continue
                clients.append(client)
                nombre = len(clients)

            print(
                f"[CONNECTÉ] Client {adresse[0]}:{adresse[1]} "
                f"({nombre}/{MAX_CLIENTS})"
            )
        except OSError:
            break


def main():
    clients = []
    verrou = threading.Lock()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as serveur:
        serveur.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        serveur.bind((HOST, PORT))
        serveur.listen(MAX_CLIENTS)

        thread = threading.Thread(
            target=accepter_clients,
            args=(serveur, clients, verrou),
            daemon=True,
        )
        thread.start()

        print(f"[SERVEUR] En écoute sur {HOST}:{PORT}...")
        print(f"[SERVEUR] Maximum : {MAX_CLIENTS} clients.")
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

            with verrou:
                clients_actuels = list(clients)

            if not clients_actuels:
                print("[INFO] Aucun client connecté.")
                continue

            for client in clients_actuels:
                connexion, lecture, adresse = client

                try:
                    envoyer(connexion, commande)
                    reponse = lecture.readline()

                    if not reponse:
                        supprimer_client(client, clients, verrou)
                    else:
                        print(
                            f"Client {adresse[0]}:{adresse[1]} : "
                            f"{reponse.rstrip()}"
                        )
                except (OSError, UnicodeError) as erreur:
                    print(f"[ERREUR] Client {adresse[0]} déconnecté : {erreur}")
                    supprimer_client(client, clients, verrou)

            if commande == "quitter":
                break


if __name__ == "__main__":
    try:
        main()
    except OSError as erreur:
        print(f"[ERREUR] Impossible de démarrer le serveur : {erreur}")
