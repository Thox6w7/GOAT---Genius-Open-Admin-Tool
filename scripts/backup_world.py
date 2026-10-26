import os
import sys
import shutil
import json
from datetime import datetime

# Détection de la racine du panel
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))

SERVERS_FILE = os.path.join(BASE_DIR, "servers.json")
BACKUPS_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUPS_DIR, exist_ok=True)

def find_target_world(target_server_arg=None):
    # 1. Si un ID ou un chemin précis est passé en paramètre
    if target_server_arg and os.path.exists(SERVERS_FILE):
        try:
            with open(SERVERS_FILE, "r", encoding="utf-8") as f:
                servers = json.load(f)
                for srv in servers:
                    if srv.get("id") == target_server_arg or srv.get("server_dir") == target_server_arg:
                        candidate = os.path.join(srv.get("server_dir", ""), "world")
                        if os.path.isdir(candidate):
                            return candidate, srv.get("name", "server")
        except Exception:
            pass

    # 2. Si le paramètre direct est un chemin de dossier
    if target_server_arg and os.path.isdir(target_server_arg):
        candidate = os.path.join(target_server_arg, "world")
        if os.path.isdir(candidate):
            return candidate, os.path.basename(target_server_arg)

    # 3. Fallback : premier serveur déclaré dans servers.json avec un dossier 'world'
    if os.path.exists(SERVERS_FILE):
        try:
            with open(SERVERS_FILE, "r", encoding="utf-8") as f:
                servers = json.load(f)
                for srv in servers:
                    s_dir = srv.get("server_dir", "")
                    candidate = os.path.join(s_dir, "world")
                    if os.path.isdir(candidate):
                        return candidate, srv.get("name", "server")
        except Exception:
            pass

    # 4. Fallback : scan automatique dans ./servers/
    servers_folder = os.path.join(BASE_DIR, "servers")
    if os.path.isdir(servers_folder):
        for entry in os.scandir(servers_folder):
            if entry.is_dir():
                candidate = os.path.join(entry.path, "world")
                if os.path.isdir(candidate):
                    return candidate, entry.name

    return None, None

def main():
    target_arg = sys.argv[1] if len(sys.argv) > 1 else None
    world_dir, srv_name = find_target_world(target_arg)

    if not world_dir:
        print("[ERREUR] Aucun dossier 'world' valide n'a été détecté.")
        print("Assurez-vous qu'un serveur a été démarré au moins une fois pour générer sa map.")
        sys.exit(1)

    clean_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in srv_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_base_name = os.path.join(BACKUPS_DIR, f"backup_{clean_name}_{timestamp}")

    print(f"[*] Démarrage de la sauvegarde du monde :")
    print(f"    Serveur source : {srv_name}")
    print(f"    Dossier : {world_dir}")
    print(f"    Cible   : {archive_base_name}.zip")

    try:
        shutil.make_archive(
            base_name=archive_base_name,
            format="zip",
            root_dir=world_dir
        )
        final_zip = f"{archive_base_name}.zip"
        size_mb = round(os.path.getsize(final_zip) / (1024 * 1024), 2)
        print(f"[SUCCÈS] Sauvegarde terminée avec succès !")
        print(f"    Fichier : {os.path.basename(final_zip)} ({size_mb} Mo)")
        print(f"    Emplacement : {final_zip}")
    except Exception as e:
        print(f"[ERREUR] Échec de la création de l'archive ZIP : {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()