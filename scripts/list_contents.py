import sys
import os
import argparse
from datetime import datetime

def format_size(size_bytes):
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(units) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {units[i]}"

def inspect(target_path):
    target = os.path.abspath(target_path)

    if not os.path.exists(target):
        print(f"[ERREUR] Le chemin n'existe pas : {target}", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print(f" CIBLE : {target}")
    print("=" * 70)

    if os.path.isfile(target):
        stats = os.stat(target)
        mod_time = datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"Type             : Fichier")
        print(f"Taille           : {format_size(stats.st_size)} ({stats.st_size:,} octets)")
        print(f"Dernière modif   : {mod_time}")
        print("=" * 70)
        return

    try:
        entries = sorted(list(os.scandir(target)), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        print(f"[ERREUR] Accès refusé pour : {target}", file=sys.stderr)
        sys.exit(1)

    total_files = 0
    total_dirs = 0
    total_size = 0

    print(f"{'TYPE':<8} | {'TAILLE':<12} | {'MODIFIÉ LE':<19} | {'NOM'}")
    print("-" * 70)

    for entry in entries:
        try:
            stats = entry.stat()
            mod_time = datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            if entry.is_dir():
                total_dirs += 1
                type_str = "[DIR]"
                size_str = "-"
            else:
                total_files += 1
                total_size += stats.st_size
                type_str = "[FILE]"
                size_str = format_size(stats.st_size)

            print(f"{type_str:<8} | {size_str:<12} | {mod_time:<19} | {entry.name}")
        except (PermissionError, FileNotFoundError):
            print(f"[???]    | {'-':<12} | {'-':<19} | {entry.name} (Inaccessible)")

    print("-" * 70)
    print(f"TOTAL : {total_dirs} dossier(s), {total_files} fichier(s) | Taille : {format_size(total_size)}")
    print("=" * 70)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default=".")
    args = parser.parse_args()
    inspect(args.path)