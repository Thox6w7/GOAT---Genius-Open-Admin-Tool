import os
import sys
import shutil
import PyInstaller.__main__

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

print("[GOAT BUILD] Nettoyage des anciens builds...")
for folder in ["build", "dist"]:
    target = os.path.join(BASE_DIR, folder)
    if os.path.exists(target):
        shutil.rmtree(target, ignore_errors=True)

public_src = os.path.join(BASE_DIR, "public")
scripts_src = os.path.join(BASE_DIR, "scripts")
icon_path = os.path.join(BASE_DIR, "GOAT.ico")

args = [
    os.path.join(BASE_DIR, "main.py"),
    "--name=GOAT",
    "--onedir",
    "--console",
    "--noconfirm",
    f"--icon={icon_path}",
    "--hidden-import=uvicorn.logging",
    "--hidden-import=uvicorn.loops",
    "--hidden-import=uvicorn.loops.auto",
    "--hidden-import=uvicorn.protocols",
    "--hidden-import=uvicorn.protocols.http",
    "--hidden-import=uvicorn.protocols.http.auto",
    "--hidden-import=uvicorn.protocols.websockets",
    "--hidden-import=uvicorn.protocols.websockets.auto",
    "--hidden-import=uvicorn.lifespans",
    "--hidden-import=uvicorn.lifespans.on",
    "--hidden-import=mcrcon",
    "--hidden-import=psutil",
    "--hidden-import=httpx",
]

print("[GOAT BUILD] Compilation PyInstaller avec GOAT.ico...")
PyInstaller.__main__.run(args)

# Synchronisation sécurisée des répertoires et fichiers dans dist/GOAT
dist_goat_dir = os.path.join(BASE_DIR, "dist", "GOAT")
dist_public = os.path.join(dist_goat_dir, "public")
dist_scripts = os.path.join(dist_goat_dir, "scripts")

print("[GOAT BUILD] Copie des fichiers statiques dans dist/GOAT/...")
if os.path.exists(dist_public):
    shutil.rmtree(dist_public)
shutil.copytree(public_src, dist_public)

if os.path.exists(dist_scripts):
    shutil.rmtree(dist_scripts)
shutil.copytree(scripts_src, dist_scripts)

# Copie de GOAT.ico à la racine de dist/GOAT pour Inno Setup
if os.path.exists(icon_path):
    shutil.copy2(icon_path, os.path.join(dist_goat_dir, "GOAT.ico"))
    print("[GOAT BUILD] Icône GOAT.ico copiée dans dist/GOAT/")
else:
    print(f"[WARN] GOAT.ico non trouvé dans {BASE_DIR}")

# Création du dossier servers prêt à l'emploi
os.makedirs(os.path.join(dist_goat_dir, "servers"), exist_ok=True)

print(f"[GOAT BUILD] Fichiers dans public : {os.listdir(dist_public)}")
print("\n[GOAT BUILD] Compilation & synchronisation terminées avec succès !")