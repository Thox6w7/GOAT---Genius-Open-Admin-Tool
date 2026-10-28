import sys
import os

# Détection automatique : mode compilé (.exe) vs mode développement (.py)
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Paramètres du serveur Web
# 127.0.0.1 = accessible uniquement depuis cette machine (recommandé).
# Mets SERVER_HOST=0.0.0.0 pour un accès réseau : le panel exige alors un mot de passe.
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8000))

# Configuration RCON Minecraft
RCON_HOST = os.getenv("MC_RCON_HOST", "127.0.0.1")
RCON_PORT = int(os.getenv("MC_RCON_PORT", 25575))
RCON_PASSWORD = os.getenv("MC_RCON_PASS", "change_me_rcon_password")

# Fichiers et dossiers
MC_LOGS_PATH = os.getenv("MC_LOGS_PATH", os.path.join(BASE_DIR, "logs", "latest.log"))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")