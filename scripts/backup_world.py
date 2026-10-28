"""Sauvegarde horodatée du dossier `world` d'un serveur Minecraft.

Usage :
    python backup_world.py [serveur]

`serveur` peut être :
    - un ID présent dans `servers.json` (ex. srv-test_forge) ;
    - le chemin du dossier d'un serveur (celui qui contient `world/`) ;
    - le chemin direct du dossier `world`.

Sans argument, le premier serveur de `servers.json` possédant un dossier
`world/` est sauvegardé (puis, à défaut, le premier trouvé dans `servers/`).
L'archive ZIP est créée dans `backups/`.
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

# Détection de la racine du panel
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))

SERVERS_FILE = os.path.join(BASE_DIR, "servers.json")
BACKUPS_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUPS_DIR, exist_ok=True)


def load_servers():
    if not os.path.exists(SERVERS_FILE):
        return []
    try:
        with open(SERVERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _normalize_path(path):
    return os.path.normcase(os.path.abspath(path)) if path else ""


def _world_from_server_dir(server_dir):
    if not server_dir:
        return None
    server_dir = os.path.abspath(server_dir)
    candidate = os.path.join(server_dir, "world")
    if os.path.isdir(candidate):
        return candidate
    # Autorise aussi un chemin pointant directement sur le dossier world.
    if os.path.isfile(os.path.join(server_dir, "level.dat")):
        return server_dir
    return None


def resolve_explicit_target(target):
    """Résout un ID, un dossier serveur ou un dossier world.

    Lève LookupError avec un message clair si la cible est invalide : on ne
    retombe jamais silencieusement sur un autre serveur.
    """
    normalized_target = _normalize_path(target)

    for srv in load_servers():
        srv_id = str(srv.get("id", ""))
        srv_dir = srv.get("server_dir", "")
        if srv_id == target or _normalize_path(srv_dir) == normalized_target:
            world = _world_from_server_dir(srv_dir)
            if not world:
                name = srv.get("name") or srv_id or target
                raise LookupError(
                    f"Le serveur « {name} » ne contient pas de dossier world/ exploitable."
                )
            return world, srv.get("name") or srv_id or os.path.basename(world)

        # Un chemin pointant directement sur le world/ d'un serveur connu
        # conserve le nom du serveur dans l'archive.
        srv_world = _world_from_server_dir(srv_dir)
        if srv_world and _normalize_path(srv_world) == normalized_target:
            return srv_world, srv.get("name") or srv_id or os.path.basename(srv_world)

    if os.path.isdir(target):
        world = _world_from_server_dir(target)
        if world:
            return world, os.path.basename(os.path.abspath(target))
        raise LookupError(
            f"Le dossier « {target} » ne contient ni sous-dossier world/ ni fichier level.dat."
        )

    raise LookupError(
        f"Aucun serveur de servers.json et aucun dossier ne correspond à « {target} »."
    )


def find_target_world(target_server_arg=None):
    """Retourne (dossier world, nom du serveur, message d'erreur éventuel)."""
    if target_server_arg:
        try:
            world_dir, srv_name = resolve_explicit_target(target_server_arg)
            return world_dir, srv_name, None
        except LookupError as exc:
            return None, None, str(exc)

    # Fallback : premier serveur déclaré dans servers.json avec un dossier world.
    for srv in load_servers():
        world = _world_from_server_dir(srv.get("server_dir", ""))
        if world:
            return world, srv.get("name") or srv.get("id") or "server", None

    # Dernier recours : scan automatique dans ./servers/.
    servers_folder = os.path.join(BASE_DIR, "servers")
    if os.path.isdir(servers_folder):
        try:
            entries = sorted(os.scandir(servers_folder), key=lambda e: e.name.lower())
        except OSError:
            entries = []
        for entry in entries:
            if entry.is_dir():
                candidate = os.path.join(entry.path, "world")
                if os.path.isdir(candidate):
                    return candidate, entry.name, None

    return None, None, None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Crée une archive ZIP horodatée du dossier world d'un serveur Minecraft."
    )
    parser.add_argument(
        "serveur",
        nargs="?",
        help="ID du serveur (servers.json), dossier du serveur ou dossier world. "
        "Sans argument : premier serveur avec un monde détecté.",
    )
    args = parser.parse_args(argv)

    world_dir, srv_name, error = find_target_world(args.serveur)

    if not world_dir:
        print(f"[ERREUR] {error}" if error else
              "[ERREUR] Aucun dossier 'world' valide n'a été détecté.", file=sys.stderr)
        if args.serveur:
            print("Indique un ID de servers.json, un dossier serveur contenant world/, "
                  "ou directement un dossier world.", file=sys.stderr)
        else:
            print("Assurez-vous qu'un serveur a été démarré au moins une fois pour "
                  "générer sa map.", file=sys.stderr)
        return 1

    clean_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in srv_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_base_name = os.path.join(BACKUPS_DIR, f"backup_{clean_name}_{timestamp}")
    suffix = 1
    while os.path.exists(f"{archive_base_name}.zip"):
        suffix += 1
        archive_base_name = os.path.join(
            BACKUPS_DIR, f"backup_{clean_name}_{timestamp}_{suffix}"
        )

    print("[*] Démarrage de la sauvegarde du monde :")
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
        print("[SUCCÈS] Sauvegarde terminée avec succès !")
        print(f"    Fichier : {os.path.basename(final_zip)} ({size_mb} Mo)")
        print(f"    Emplacement : {final_zip}")
        return 0
    except Exception as exc:
        print(f"[ERREUR] Échec de la création de l'archive ZIP : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
