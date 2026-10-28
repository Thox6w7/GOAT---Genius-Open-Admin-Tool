import asyncio
import ast
import json
import os
import re
import glob
import shutil
import stat
import threading
import psutil
import subprocess
import sys
from datetime import datetime
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from mcrcon import MCRcon

import ark_installer
import config
import mc_installer
import security

app = FastAPI(title="Genius Open Admin Tool (GOAT) API", version="pre-alpha")

SERVERS_FILE = os.path.join(config.BASE_DIR, "servers.json")
SERVERS_DIR = os.path.join(config.BASE_DIR, "servers")
os.makedirs(SERVERS_DIR, exist_ok=True)

LOADERS = {"vanilla", "forge", "neoforge", "sponge"}
RAM_RE = re.compile(r"^[1-9]\d{0,2}[MG]$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z ._()+\-]{0,63}$")
MAX_LISTING_ENTRIES = 50_000
SCRIPT_TIMEOUT_SECONDS = int(os.getenv("GOAT_SCRIPT_TIMEOUT", "300"))
SCRIPT_MAX_OUTPUT_BYTES = int(os.getenv("GOAT_SCRIPT_MAX_OUTPUT", "1000000"))
SCRIPT_MAX_ARGS = 32
SCRIPT_MAX_ARG_LENGTH = 1024
SCRIPT_MAX_TOTAL_ARGS_LENGTH = 8192

active_log_sockets = {}
_servers_lock = threading.RLock()


# --- 0. AUTHENTIFICATION & GARDE-FOUS HTTP ---
PUBLIC_PATHS = {
    "/login.html",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/logout",
}


def client_is_local(request: Request):
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return urlparse(origin).netloc == request.headers.get("host", "")


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request):
        return JSONResponse(
            {"status": "error", "message": "Origine non autorisée."}, status_code=403
        )

    path = request.url.path
    if path in PUBLIC_PATHS:
        return await call_next(request)

    if security.valid_session(request.cookies.get(security.SESSION_COOKIE)):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse(
            {"status": "error", "code": "unauthorized", "message": "Authentification requise."},
            status_code=401,
        )

    if path == "/" or path.endswith(".html"):
        return RedirectResponse(f"/login.html?next={quote(path)}", status_code=303)

    return JSONResponse(
        {"status": "error", "code": "unauthorized", "message": "Authentification requise."},
        status_code=401,
    )


def ws_authorized(websocket: WebSocket) -> bool:
    if not security.valid_session(websocket.cookies.get(security.SESSION_COOKIE)):
        return False
    origin = websocket.headers.get("origin")
    if origin and urlparse(origin).netloc != websocket.headers.get("host", ""):
        return False
    return True


class LoginPayload(BaseModel):
    password: str


class PasswordChangePayload(BaseModel):
    current: str
    new: str


def _set_session_cookie(response, request: Request):
    response.set_cookie(
        key=security.SESSION_COOKIE,
        value=security.issue_session(),
        max_age=security.SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@app.get("/api/auth/status")
def auth_status(request: Request):
    return {
        "setup": security.password_is_set(),
        "authenticated": security.valid_session(request.cookies.get(security.SESSION_COOKIE)),
        "local": client_is_local(request),
    }


@app.post("/api/auth/setup")
def auth_setup(payload: LoginPayload, request: Request):
    if security.password_is_set():
        return JSONResponse(
            {"status": "error", "message": "Un mot de passe est déjà configuré."}, status_code=409
        )
    if not client_is_local(request):
        return JSONResponse(
            {
                "status": "error",
                "message": "La configuration initiale doit se faire depuis la machine qui héberge le panel.",
            },
            status_code=403,
        )
    ok, message = security.set_password(payload.password)
    if not ok:
        return JSONResponse({"status": "error", "message": message}, status_code=400)
    response = JSONResponse({"status": "ok", "message": message})
    return _set_session_cookie(response, request)


@app.post("/api/auth/login")
def auth_login(payload: LoginPayload, request: Request):
    ip = request.client.host if request.client else "unknown"
    if not security.password_is_set():
        return JSONResponse(
            {"status": "error", "code": "setup_required", "message": "Aucun mot de passe configuré."},
            status_code=409,
        )
    if security.too_many_attempts(ip):
        return JSONResponse(
            {"status": "error", "message": "Trop de tentatives. Réessaie dans quelques minutes."},
            status_code=429,
        )
    if not security.verify_password(payload.password):
        security.record_failure(ip)
        return JSONResponse(
            {"status": "error", "message": "Mot de passe incorrect."}, status_code=401
        )
    security.clear_failures(ip)
    response = JSONResponse({"status": "ok"})
    return _set_session_cookie(response, request)


@app.post("/api/auth/logout")
def auth_logout():
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return response


@app.post("/api/auth/password")
def auth_change_password(payload: PasswordChangePayload, request: Request):
    if not security.verify_password(payload.current):
        return JSONResponse(
            {"status": "error", "message": "Mot de passe actuel incorrect."}, status_code=401
        )
    ok, message = security.set_password(payload.new)
    if not ok:
        return JSONResponse({"status": "error", "message": message}, status_code=400)
    security.rotate_secret()
    response = JSONResponse({"status": "ok", "message": message})
    return _set_session_cookie(response, request)


# --- 0.1 OUTILS SYSTEME ---
def is_within(child: str, parent: str) -> bool:
    try:
        child_abs = os.path.abspath(child)
        parent_abs = os.path.abspath(parent)
        return os.path.commonpath([child_abs, parent_abs]) == parent_abs
    except ValueError:
        return False


def valid_ram(value: str) -> bool:
    return bool(RAM_RE.match((value or "").strip()))


def remove_readonly(func, path, excinfo):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


ARK_EXECUTABLES = {"shootergameserver.exe", "arkascendedserver.exe"}


def scan_server_processes():
    """Retourne les process de serveurs par jeu.

    Minecraft : clé = dossier de travail (cwd) du process Java.
    ARK : clé = chemin de l'exécutable du serveur.
    """
    found = {"minecraft": {}, "ark": {}}
    for proc in psutil.process_iter(["pid", "name", "cwd", "exe"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "java" in name:
                cwd = proc.info.get("cwd") or ""
                if cwd:
                    found["minecraft"][os.path.abspath(cwd).lower()] = proc
            elif name in ARK_EXECUTABLES:
                exe = proc.info.get("exe") or ""
                if exe:
                    found["ark"][os.path.abspath(exe).lower()] = proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def server_process(server_dir: str, game: str = "minecraft", scanned=None):
    if not server_dir:
        return None
    base = os.path.abspath(server_dir).lower()
    scanned = scanned or scan_server_processes()
    if game == "ark":
        for exe_path, proc in scanned["ark"].items():
            if is_within(exe_path, base):
                return proc
        return None
    return scanned["minecraft"].get(base)


def public_server(srv: dict, scanned=None) -> dict:
    data = {k: v for k, v in srv.items() if k not in ("rcon_pass", "admin_pass")}
    server_dir = srv.get("server_dir") or ""
    game = srv.get("game", "minecraft")
    scanned = scanned or scan_server_processes()
    is_up = server_process(server_dir, game, scanned) is not None
    if not server_dir or not os.path.isdir(server_dir):
        data["status"] = "missing"
    else:
        data["status"] = "online" if is_up else "stopped"
    data["has_rcon_pass"] = bool(srv.get("rcon_pass"))
    data["has_admin_pass"] = bool(srv.get("admin_pass"))
    data["game"] = game
    return data


def kill_server_processes(server_dir):
    base = os.path.abspath(server_dir).lower()
    for proc in psutil.process_iter(["pid", "name", "cwd", "exe"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cwd = proc.info.get("cwd") or ""
            exe = proc.info.get("exe") or ""
            is_minecraft = "java" in name and cwd and is_within(cwd, base)
            is_ark = name in ARK_EXECUTABLES and exe and is_within(exe, base)
            if is_minecraft or is_ark:
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def apply_ram_to_server_files(server_dir: str, ram_max: str):
    if not os.path.isdir(server_dir) or not valid_ram(ram_max):
        return

    jvm_args_path = os.path.join(server_dir, "user_jvm_args.txt")
    if os.path.exists(jvm_args_path):
        try:
            with open(jvm_args_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "-Xmx" in content:
                content = re.sub(r"-Xmx\s*\d+[kKmMgG]?", f"-Xmx{ram_max}", content)
            else:
                content += f"\n-Xmx{ram_max}\n"
            with open(jvm_args_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            pass

    for bat_file in glob.glob(os.path.join(server_dir, "*.bat")):
        try:
            with open(bat_file, "r", encoding="utf-8", errors="ignore") as f:
                bat_content = f.read()
            if "-Xmx" in bat_content:
                new_bat = re.sub(r"-Xmx\s*\d+[kKmMgG]?", f"-Xmx{ram_max}", bat_content)
                with open(bat_file, "w", encoding="utf-8") as f:
                    f.write(new_bat)
        except Exception:
            pass


def find_server_executable(target_dir):
    if not os.path.isdir(target_dir):
        return None, None, None

    priority_bats = ["goat_launch.bat", "run.bat", "start.bat", "launch.bat", "server.bat"]
    for b in priority_bats:
        full_path = os.path.join(target_dir, b)
        if os.path.isfile(full_path):
            return "bat", full_path, target_dir

    for entry in os.scandir(target_dir):
        if entry.is_file() and entry.name.lower().endswith(".bat"):
            return "bat", entry.path, target_dir

    priority_jars = ["server.jar", "forge.jar", "neoforge.jar", "paper.jar"]
    for j in priority_jars:
        full_path = os.path.join(target_dir, j)
        if os.path.isfile(full_path):
            return "jar", full_path, target_dir

    for entry in os.scandir(target_dir):
        if entry.is_file() and entry.name.lower().endswith(".jar") and not entry.name.lower().endswith("-installer.jar"):
            return "jar", entry.path, target_dir

    return None, None, None


def detect_game(server_dir: str) -> str:
    """Devine le jeu d'un dossier : ARK si structure ShooterGame, sinon Minecraft."""
    try:
        if os.path.isdir(os.path.join(server_dir, "ShooterGame")):
            return "ark"
        for name in ("ArkAscendedServer.exe", "ShooterGameServer.exe"):
            if os.path.isfile(os.path.join(server_dir, "ShooterGame", "Binaries", "Win64", name)):
                return "ark"
    except OSError:
        pass
    return "minecraft"


def find_ark_launcher(target_dir):
    """Retourne (type, chemin, dossier) pour lancer un serveur ARK."""
    if not os.path.isdir(target_dir):
        return None, None, None

    for name in ("start_ark.bat", "ark.bat", "goat_ark.bat", "run.bat", "start.bat", "launch.bat"):
        full_path = os.path.join(target_dir, name)
        if os.path.isfile(full_path):
            return "bat", full_path, target_dir

    for entry in os.scandir(target_dir):
        if not entry.is_file() or not entry.name.lower().endswith(".bat"):
            continue
        try:
            with open(entry.path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(4096).lower()
            if "shootergameserver" in content or "arkascendedserver" in content:
                return "bat", entry.path, target_dir
        except OSError:
            continue

    for rel in (
        ("ShooterGame", "Binaries", "Win64", "ArkAscendedServer.exe"),
        ("ShooterGame", "Binaries", "Win64", "ShooterGameServer.exe"),
    ):
        full_path = os.path.join(target_dir, *rel)
        if os.path.isfile(full_path):
            return "exe", full_path, os.path.dirname(full_path)

    return None, None, None


def server_log_paths(srv: dict):
    server_dir = srv.get("server_dir") or ""
    if srv.get("game", "minecraft") == "ark":
        logs_dir = os.path.join(server_dir, "ShooterGame", "Saved", "Logs")
        return [
            os.path.join(logs_dir, "ShooterGame.log"),
            os.path.join(logs_dir, "ShooterGameBackup.log"),
        ]
    return [os.path.join(server_dir, "logs", "latest.log")]


def load_servers():
    servers = []
    if os.path.exists(SERVERS_FILE):
        try:
            with open(SERVERS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for s in saved:
                    s_dir = os.path.abspath(s.get("server_dir", ""))
                    if "ram_max" not in s:
                        s["ram_max"] = "4G"
                    if "game" not in s:
                        s["game"] = detect_game(s_dir) if os.path.isdir(s_dir) else "minecraft"
                    if s["game"] == "ark":
                        s.setdefault("map", "TheIsland")
                        s.setdefault("query_port", 27015)
                    # Un dossier temporairement inaccessible ne doit pas effacer
                    # la configuration du serveur (mot de passe RCON, ports...).
                    if os.path.isdir(s_dir):
                        s.pop("missing", None)
                    else:
                        s["missing"] = True
                    servers.append(s)
        except Exception:
            servers = []

    if os.path.exists(SERVERS_DIR):
        for entry in os.scandir(SERVERS_DIR):
            if entry.is_dir():
                # Installation SteamCMD en cours : le serveur n'est pas encore déclaré.
                if os.path.exists(os.path.join(entry.path, ark_installer.INSTALL_MARKER)):
                    continue
                srv_id = f"srv-{entry.name}"
                existing = next((s for s in servers if s.get("id") == srv_id), None)
                if not existing:
                    game = detect_game(entry.path)
                    if game == "ark":
                        used_rcon = {int(s.get("rcon_port", 0)) for s in servers if s.get("game") == "ark"}
                        used_game = {int(s.get("server_port", 0)) for s in servers if s.get("game") == "ark"}
                        used_query = {int(s.get("query_port", 0)) for s in servers if s.get("game") == "ark"}
                        offset = 0
                        while (
                            (27020 + offset) in used_rcon
                            or (7777 + offset) in used_game
                            or (27015 + offset) in used_query
                        ):
                            offset += 1
                        servers.append({
                            "id": srv_id,
                            "name": entry.name.replace("_", " ").capitalize(),
                            "game": "ark",
                            "loader": "ark",
                            "map": "TheIsland",
                            "query_port": 27015 + offset,
                            "rcon_host": "127.0.0.1",
                            "rcon_port": 27020 + offset,
                            "server_port": 7777 + offset,
                            "rcon_pass": config.RCON_PASSWORD,
                            "server_dir": os.path.abspath(entry.path),
                            "ram_max": "",
                            "status": "ready"
                        })
                        continue

                    used_rcon = [s.get("rcon_port", 25575) for s in servers]
                    next_rcon = 25575
                    while next_rcon in used_rcon:
                        next_rcon += 1

                    servers.append({
                        "id": srv_id,
                        "name": entry.name.replace("_", " ").capitalize(),
                        "game": "minecraft",
                        "rcon_host": "127.0.0.1",
                        "rcon_port": next_rcon,
                        "server_port": 25565 + (next_rcon - 25575),
                        "rcon_pass": config.RCON_PASSWORD,
                        "server_dir": os.path.abspath(entry.path),
                        "ram_max": "4G",
                        "status": "ready"
                    })
                else:
                    existing["server_dir"] = os.path.abspath(entry.path)

    save_servers(servers)
    return servers


def save_servers(servers):
    tmp = f"{SERVERS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(servers, f, indent=4)
    os.replace(tmp, SERVERS_FILE)


servers_db = load_servers()


class CommandPayload(BaseModel):
    server_id: str
    command: str


class ServerStartPayload(BaseModel):
    server_id: str
    server_dir: str = ""


class ServerActionPayload(BaseModel):
    server_id: str


class DifficultyPayload(BaseModel):
    server_id: str
    difficulty: str


class ScriptPayload(BaseModel):
    script_name: str
    args: list[str] = []


class AutoInstallPayload(BaseModel):
    name: str
    loader: str
    version: str
    rcon_pass: str = "nexus_pass"
    ram_max: str = "4G"


class ImportServerPayload(BaseModel):
    name: str
    server_dir: str
    rcon_port: int = 25575
    rcon_pass: str = "nexus_pass"
    ram_max: str = "4G"


class ArkImportPayload(BaseModel):
    name: str
    server_dir: str
    map: str = "TheIsland"
    server_port: int = 7777
    query_port: int = 27015
    rcon_port: int = 27020
    rcon_pass: str = "nexus_pass"


class ArkCreatePayload(BaseModel):
    name: str
    edition: str = "ase"
    map: str = "TheIsland"
    server_port: int = 0
    query_port: int = 0
    rcon_port: int = 0
    rcon_pass: str = "nexus_pass"
    admin_pass: str = ""
    max_players: int = 70


class PropertiesSavePayload(BaseModel):
    server_id: str
    content: str


class RamUpdatePayload(BaseModel):
    server_id: str
    ram_max: str


# --- 1. GESTION DES SERVEURS, IMPORT, RAM & SUPPRESSION ---
@app.get("/api/mc/servers")
def get_servers():
    global servers_db
    with _servers_lock:
        servers_db = load_servers()
        snapshot = [dict(s) for s in servers_db if s.get("game", "minecraft") == "minecraft"]
    scanned = scan_server_processes()
    return [public_server(s, scanned) for s in snapshot]


@app.get("/api/ark/servers")
def get_ark_servers():
    global servers_db
    with _servers_lock:
        servers_db = load_servers()
        snapshot = [dict(s) for s in servers_db if s.get("game") == "ark"]
    scanned = scan_server_processes()
    return [public_server(s, scanned) for s in snapshot]


@app.post("/api/mc/servers/ram")
def update_server_ram(payload: RamUpdatePayload):
    global servers_db
    if not valid_ram(payload.ram_max):
        return {"status": "error", "message": "Format RAM invalide (exemples : 4G, 8192M)."}
    with _servers_lock:
        srv = next((s for s in servers_db if s["id"] == payload.server_id), None)
        if not srv:
            return {"status": "error", "message": "Serveur introuvable."}

        srv["ram_max"] = payload.ram_max.strip()
        apply_ram_to_server_files(srv.get("server_dir", ""), srv["ram_max"])
        save_servers(servers_db)

    return {"status": "ok", "message": f"RAM allouée fixée à {payload.ram_max} pour {srv['name']}."}


def _forbidden_import_dirs():
    protected = [config.BASE_DIR, os.environ.get("SystemRoot", r"C:\Windows")]
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        value = os.environ.get(env)
        if value:
            protected.append(value)
    return [os.path.abspath(p) for p in protected if p]


@app.post("/api/mc/servers/import")
def import_server(payload: ImportServerPayload):
    global servers_db
    name = (payload.name or "").strip()
    if not name or len(name) > 64 or "\n" in name or "\r" in name:
        return {"status": "error", "message": "Nom de serveur invalide (1 à 64 caractères, sans retour à la ligne)."}

    target_dir = os.path.abspath(payload.server_dir.strip().strip('"').strip("'"))

    if not os.path.isdir(target_dir):
        return {"status": "error", "message": f"Dossier introuvable sur le disque : {target_dir}"}

    if target_dir == os.path.abspath(os.sep):
        return {"status": "error", "message": "Impossible d'importer la racine d'un disque."}

    for protected in _forbidden_import_dirs():
        if is_within(target_dir, protected) and target_dir != protected:
            return {
                "status": "error",
                "message": f"Import refusé : {target_dir} se trouve dans un dossier protégé ({protected}).",
            }

    exec_type, target_file, _ = find_server_executable(target_dir)
    if not exec_type:
        return {"status": "error", "message": f"Aucun fichier run.bat ou server.jar trouvé dans : {target_dir}"}

    if detect_game(target_dir) == "ark":
        return {"status": "error", "message": "Ce dossier contient un serveur ARK : utilise l'onglet ARK."}

    if not valid_ram(payload.ram_max):
        return {"status": "error", "message": "Format RAM invalide (exemples : 4G, 8192M)."}

    folder_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name.lower())
    srv_id = f"srv-imp-{folder_name}"

    with _servers_lock:
        if any(s.get("id") == srv_id or os.path.abspath(s.get("server_dir", "")) == target_dir for s in servers_db):
            return {"status": "error", "message": "Ce dossier de serveur est déjà enregistré dans le panel."}

        used_rcon = [s.get("rcon_port", 25575) for s in servers_db]
        final_rcon = payload.rcon_port
        while final_rcon in used_rcon:
            final_rcon += 1

        apply_ram_to_server_files(target_dir, payload.ram_max)

        new_srv = {
            "id": srv_id,
            "name": name,
            "game": "minecraft",
            "loader": "imported",
            "version": "custom",
            "rcon_host": "127.0.0.1",
            "rcon_port": final_rcon,
            "server_port": 25565 + (final_rcon - 25575),
            "rcon_pass": payload.rcon_pass,
            "server_dir": target_dir,
            "ram_max": payload.ram_max,
            "status": "ready"
        }

        servers_db.append(new_srv)
        save_servers(servers_db)

    return {"status": "ok", "server": public_server(new_srv)}


@app.post("/api/ark/servers/import")
def import_ark_server(payload: ArkImportPayload):
    global servers_db
    name = (payload.name or "").strip()
    if not name or len(name) > 64 or "\n" in name or "\r" in name:
        return {"status": "error", "message": "Nom de serveur invalide (1 à 64 caractères)."}

    target_dir = os.path.abspath(payload.server_dir.strip().strip('"').strip("'"))
    if not os.path.isdir(target_dir):
        return {"status": "error", "message": f"Dossier introuvable sur le disque : {target_dir}"}
    if target_dir == os.path.abspath(os.sep):
        return {"status": "error", "message": "Impossible d'importer la racine d'un disque."}
    for protected in _forbidden_import_dirs():
        if is_within(target_dir, protected) and target_dir != protected:
            return {
                "status": "error",
                "message": f"Import refusé : {target_dir} se trouve dans un dossier protégé ({protected}).",
            }

    launcher_type, launcher, _ = find_ark_launcher(target_dir)
    if not launcher:
        return {
            "status": "error",
            "message": "Aucun serveur ARK détecté : il faut un dossier ShooterGame, un lanceur .bat ou l'exécutable ArkAscendedServer/ShooterGameServer.",
        }

    if detect_game(target_dir) != "ark":
        return {"status": "error", "message": "Ce dossier ne ressemble pas à une installation ARK."}

    map_name = (payload.map or "").strip()
    if not re.match(r"^[A-Za-z0-9_\-\.]{1,64}$", map_name):
        return {"status": "error", "message": "Nom de map invalide (exemple : TheIsland, TheIsland_WP, Ragnarok)."}

    for label, port in (("jeu", payload.server_port), ("query", payload.query_port), ("RCON", payload.rcon_port)):
        if not (1 <= int(port) <= 65535):
            return {"status": "error", "message": f"Port {label} invalide."}
    if len({payload.server_port, payload.query_port, payload.rcon_port}) != 3:
        return {"status": "error", "message": "Les ports jeu, query et RCON doivent être différents."}
    if not (4 <= len(payload.rcon_pass) <= 64) or "\r" in payload.rcon_pass or "\n" in payload.rcon_pass:
        return {"status": "error", "message": "Mot de passe RCON invalide (4 à 64 caractères)."}

    folder_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name.lower())
    srv_id = f"srv-ark-{folder_name}"

    with _servers_lock:
        if any(
            s.get("id") == srv_id
            or os.path.abspath(s.get("server_dir", "")) == target_dir
            for s in servers_db
        ):
            return {"status": "error", "message": "Ce dossier de serveur est déjà enregistré dans le panel."}

        for s in servers_db:
            if s.get("game") != "ark":
                continue
            if int(s.get("rcon_port", 0)) == int(payload.rcon_port):
                return {"status": "error", "message": f"Le port RCON {payload.rcon_port} est déjà utilisé par {s['name']}."}
            if int(s.get("server_port", 0)) == int(payload.server_port):
                return {"status": "error", "message": f"Le port de jeu {payload.server_port} est déjà utilisé par {s['name']}."}
            if int(s.get("query_port", 0)) == int(payload.query_port):
                return {"status": "error", "message": f"Le port Query {payload.query_port} est déjà utilisé par {s['name']}."}

        new_srv = {
            "id": srv_id,
            "name": name,
            "game": "ark",
            "loader": "ark",
            "map": map_name,
            "server_port": int(payload.server_port),
            "query_port": int(payload.query_port),
            "rcon_host": "127.0.0.1",
            "rcon_port": int(payload.rcon_port),
            "rcon_pass": payload.rcon_pass,
            "server_dir": target_dir,
            "ram_max": "",
            "launcher": launcher_type,
            "status": "ready"
        }
        servers_db.append(new_srv)
        save_servers(servers_db)

    return {"status": "ok", "server": public_server(new_srv)}


# --- 1bis. CRÉATION D'UN SERVEUR ARK (STEAMCMD + API STEAM) ---
def _listening_ports():
    ports = set()
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr:
                ports.add(int(conn.laddr.port))
    except Exception:
        pass
    return ports


def allocate_ark_ports():
    """Trio (jeu, query, RCON) libre pour un nouveau serveur ARK."""
    with _servers_lock:
        taken = set()
        for s in servers_db:
            if s.get("game") != "ark":
                continue
            for key in ("server_port", "query_port", "rcon_port"):
                try:
                    taken.add(int(s.get(key)))
                except (TypeError, ValueError):
                    continue
    busy = taken | _listening_ports()
    offset = 0
    while offset < 2000:
        trio = (7777 + offset, 27015 + offset, 27020 + offset)
        if not busy.intersection(trio):
            return trio
        offset += 1
    raise RuntimeError("Aucun port ARK libre trouvé.")


@app.get("/api/ark/editions")
async def get_ark_editions():
    return await ark_installer.editions_payload()


@app.post("/api/ark/steamcmd/install")
def install_steamcmd_api():
    job, error = ark_installer.start_steamcmd_job()
    if error:
        return {"status": "error", "message": error}
    return {"status": "ok", "job": ark_installer.public_job(job)}


@app.get("/api/ark/install/status")
def ark_install_status(job_id: str = ""):
    return {"status": "ok", "job": ark_installer.get_status(job_id)}


@app.post("/api/ark/servers/create")
async def create_ark_server(payload: ArkCreatePayload):
    global servers_db
    name = (payload.name or "").strip()
    if not name or len(name) > 64 or "\n" in name or "\r" in name:
        return {"status": "error", "message": "Nom de serveur invalide (1 à 64 caractères, sans retour à la ligne)."}

    edition = (payload.edition or "").strip().lower()
    if edition not in ark_installer.EDITIONS:
        return {"status": "error", "message": "Édition ARK inconnue : choisis ASE ou ASA."}

    map_id = (payload.map or "").strip()
    if not re.match(r"^[A-Za-z0-9_\-\.]{1,64}$", map_id):
        return {"status": "error", "message": "Nom de map invalide (exemple : TheIsland, TheIsland_WP)."}

    if not (4 <= len(payload.rcon_pass) <= 64) or "\r" in payload.rcon_pass or "\n" in payload.rcon_pass:
        return {"status": "error", "message": "Mot de passe RCON invalide (4 à 64 caractères)."}
    if len(payload.admin_pass) > 64 or "\r" in payload.admin_pass or "\n" in payload.admin_pass:
        return {"status": "error", "message": "Mot de passe admin invalide (64 caractères maximum)."}
    if not (1 <= int(payload.max_players) <= 200):
        return {"status": "error", "message": "Nombre de joueurs invalide (1 à 200)."}

    requested = (payload.server_port, payload.query_port, payload.rcon_port)
    if any(requested):
        if not all(requested):
            return {"status": "error", "message": "Renseigne les trois ports, ou laisse les trois vides pour un choix automatique."}
        for label, port in zip(("jeu", "query", "RCON"), requested):
            if not (1 <= int(port) <= 65535):
                return {"status": "error", "message": f"Port {label} invalide."}
        if len({int(p) for p in requested}) != 3:
            return {"status": "error", "message": "Les ports jeu, query et RCON doivent être différents."}
        busy = _listening_ports()
        with _servers_lock:
            for s in servers_db:
                if s.get("game") == "ark":
                    for key in ("server_port", "query_port", "rcon_port"):
                        try:
                            busy.add(int(s.get(key)))
                        except (TypeError, ValueError):
                            continue
        clash = sorted({int(p) for p in requested}.intersection(busy))
        if clash:
            return {
                "status": "error",
                "message": f"Port(s) déjà utilisé(s) : {', '.join(str(p) for p in clash)}. Laisse les champs vides pour un choix automatique.",
            }
        server_port, query_port, rcon_port = (int(p) for p in requested)
    else:
        try:
            server_port, query_port, rcon_port = allocate_ark_ports()
        except RuntimeError as exc:
            return {"status": "error", "message": str(exc)}

    folder_name = "ark_" + re.sub(r'[^a-zA-Z0-9_\-]', '_', name.lower())
    srv_id = f"srv-{folder_name}"
    server_path = os.path.abspath(os.path.join(SERVERS_DIR, folder_name))

    with _servers_lock:
        servers_db = load_servers()
        if any(s.get("id") == srv_id for s in servers_db):
            return {"status": "error", "message": f"Un serveur ARK nommé {name} existe déjà."}
        if any(os.path.abspath(s.get("server_dir", "")) == server_path for s in servers_db):
            return {"status": "error", "message": "Ce dossier de serveur est déjà enregistré dans le panel."}
        if os.path.isdir(server_path) and os.listdir(server_path):
            if not os.path.exists(os.path.join(server_path, ark_installer.INSTALL_MARKER)):
                return {"status": "error", "message": f"Le dossier {server_path} existe déjà et n'est pas vide."}

    app_ids, app_labels, warnings = await ark_installer.resolve_map_apps(edition, map_id)

    new_srv = {
        "id": srv_id,
        "name": name,
        "game": "ark",
        "loader": "ark",
        "edition": edition,
        "map": map_id,
        "server_port": server_port,
        "query_port": query_port,
        "rcon_host": "127.0.0.1",
        "rcon_port": rcon_port,
        "rcon_pass": payload.rcon_pass,
        "admin_pass": payload.admin_pass,
        "max_players": int(payload.max_players),
        "server_dir": server_path,
        "ram_max": "",
    }

    def _register(job):
        global servers_db
        with _servers_lock:
            current = load_servers()
            current = [s for s in current if s.get("id") != srv_id]
            current.append(dict(new_srv))
            save_servers(current)
            servers_db = current

    params = {
        "name": name,
        "edition": edition,
        "map": map_id,
        "server_port": server_port,
        "query_port": query_port,
        "rcon_port": rcon_port,
        "rcon_pass": payload.rcon_pass,
        "admin_pass": payload.admin_pass,
        "max_players": int(payload.max_players),
        "server_dir": server_path,
        "server_id": srv_id,
        "app_ids": app_ids,
        "app_labels": app_labels,
        "warnings": warnings,
    }

    job, error = ark_installer.start_server_job(params, _register)
    if error:
        return {"status": "error", "message": error}

    return {
        "status": "ok",
        "message": f"Installation de {name} lancée : SteamCMD télécharge {len(app_ids)} application(s).",
        "job": ark_installer.public_job(job),
        "preview": {
            "id": srv_id,
            "name": name,
            "edition": edition,
            "map": map_id,
            "server_port": server_port,
            "query_port": query_port,
            "rcon_port": rcon_port,
            "server_dir": server_path,
        },
    }


@app.delete("/api/mc/servers/{server_id}")
async def delete_server(server_id: str, delete_files: bool = False):
    return await _delete_server(server_id, delete_files, "minecraft")


async def _delete_server(server_id: str, delete_files: bool, expected_game: str):
    global servers_db
    with _servers_lock:
        srv = next((s for s in servers_db if s["id"] == server_id), None)
        if not srv:
            return {"status": "error", "message": f"Serveur introuvable : {server_id}"}
        if expected_game and srv.get("game", "minecraft") != expected_game:
            return {"status": "error", "message": "Ce serveur n'appartient pas à cet onglet."}
        srv = dict(srv)

    target_dir = os.path.abspath(srv.get("server_dir", ""))

    if server_id in active_log_sockets:
        for ws in list(active_log_sockets.get(server_id, set())):
            try:
                await ws.close()
            except Exception:
                pass

    try:
        with MCRcon(srv["rcon_host"], srv["rcon_pass"], port=srv["rcon_port"], timeout=2) as mcr:
            mcr.command("SaveWorld" if srv.get("game") == "ark" else "save-all")
            mcr.command("DoExit" if srv.get("game") == "ark" else "stop")
    except Exception:
        pass

    if os.path.isdir(target_dir):
        kill_server_processes(target_dir)

    for _ in range(20):
        if server_process(target_dir, srv.get("game", "minecraft")) is None:
            break
        await asyncio.sleep(0.25)

    deleted_from_disk = False
    disk_note = ""
    if delete_files and os.path.isdir(target_dir):
        if not is_within(target_dir, SERVERS_DIR):
            disk_note = " Fichiers conservés : ce dossier est hors de servers/."
        else:
            try:
                shutil.rmtree(target_dir, onerror=remove_readonly)
                deleted_from_disk = True
            except Exception as e:
                return {"status": "error", "message": f"Erreur suppression disque : {str(e)}"}

    with _servers_lock:
        servers_db = [s for s in servers_db if s["id"] != server_id]
        save_servers(servers_db)

    detail = " et fichiers supprimés du disque." if deleted_from_disk else " (conservé sur le disque)."
    return {"status": "ok", "message": f"Serveur {srv['name']} retiré{detail}{disk_note}"}


@app.delete("/api/ark/servers/{server_id}")
async def delete_ark_server(server_id: str, delete_files: bool = False):
    return await _delete_server(server_id, delete_files, "ark")


# --- 2. EDITION DE SERVER.PROPERTIES ---
@app.get("/api/mc/properties")
def get_properties(server_id: str):
    global servers_db
    srv = next((s for s in servers_db if s["id"] == server_id), None)
    if not srv or not srv.get("server_dir"):
        return {"status": "error", "message": "Serveur introuvable"}

    props_file = os.path.join(srv["server_dir"], "server.properties")
    if not os.path.exists(props_file):
        return {"status": "error", "message": "Fichier server.properties introuvable dans ce serveur."}

    try:
        with open(props_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"status": "ok", "content": content}
    except Exception as e:
        return {"status": "error", "message": f"Impossible de lire le fichier : {str(e)}"}


@app.post("/api/mc/properties")
def save_properties(payload: PropertiesSavePayload):
    global servers_db
    if len(payload.content) > 1_000_000:
        return {"status": "error", "message": "Contenu trop volumineux (1 Mo max)."}

    srv = next((s for s in servers_db if s["id"] == payload.server_id), None)
    if not srv or not srv.get("server_dir"):
        return {"status": "error", "message": "Serveur introuvable"}

    props_file = os.path.join(srv["server_dir"], "server.properties")
    try:
        with open(props_file, "w", encoding="utf-8") as f:
            f.write(payload.content)
        return {"status": "ok", "message": "server.properties enregistré avec succès !"}
    except Exception as e:
        return {"status": "error", "message": f"Erreur écriture : {str(e)}"}


# --- 3. TELECHARGEMENT & VERSIONS DISTANTES ---
@app.get("/api/mc/remote-versions")
async def get_remote_versions(loader: str):
    if loader.lower() not in LOADERS:
        return {"status": "error", "message": f"Loader inconnu : {loader}", "versions": []}
    versions = await mc_installer.get_loader_versions(loader)
    return {"loader": loader, "versions": versions}


@app.post("/api/mc/servers/install")
async def install_server(payload: AutoInstallPayload):
    global servers_db
    name = (payload.name or "").strip()
    if not name or len(name) > 64 or "\n" in name or "\r" in name:
        return {"status": "error", "message": "Nom de serveur invalide (1 à 64 caractères)."}
    if payload.loader.lower() not in LOADERS:
        return {"status": "error", "message": f"Loader inconnu : {payload.loader}"}
    if not VERSION_RE.match(payload.version.strip()):
        return {"status": "error", "message": "Version invalide."}
    if not valid_ram(payload.ram_max):
        return {"status": "error", "message": "Format RAM invalide (exemples : 4G, 8192M)."}
    if not (4 <= len(payload.rcon_pass) <= 64) or "\r" in payload.rcon_pass or "\n" in payload.rcon_pass:
        return {"status": "error", "message": "Mot de passe RCON invalide (4 à 64 caractères)."}

    folder_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name.lower())
    srv_id = f"srv-{folder_name}"
    server_path = os.path.abspath(os.path.join(SERVERS_DIR, folder_name))

    with _servers_lock:
        if any(s.get("id") == srv_id for s in servers_db):
            return {"status": "error", "message": f"Un serveur nommé {name} existe déjà."}

        existing_rcon_ports = [s.get("rcon_port", 25575) for s in servers_db]
        new_rcon_port = 25575
        while new_rcon_port in existing_rcon_ports:
            new_rcon_port += 1

    new_server_port = 25565 + (new_rcon_port - 25575)

    try:
        await mc_installer.setup_server_files(
            server_dir=server_path,
            loader=payload.loader,
            version=payload.version,
            rcon_port=new_rcon_port,
            server_port=new_server_port,
            rcon_pass=payload.rcon_pass,
            ram_max=payload.ram_max
        )

        new_srv = {
            "id": srv_id,
            "name": name,
            "game": "minecraft",
            "loader": payload.loader,
            "version": payload.version,
            "rcon_host": "127.0.0.1",
            "rcon_port": new_rcon_port,
            "server_port": new_server_port,
            "rcon_pass": payload.rcon_pass,
            "server_dir": server_path,
            "ram_max": payload.ram_max,
            "status": "ready"
        }
        with _servers_lock:
            servers_db.append(new_srv)
            save_servers(servers_db)

        return {"status": "ok", "server": public_server(new_srv)}
    except Exception as e:
        return {"status": "error", "message": f"Échec création : {str(e)}"}


# --- 4. COMMANDES RCON & PROCESSUS ---
def _resolve_server(server_id: str, expected_game: str):
    srv = next((s for s in servers_db if s["id"] == server_id), None)
    if not srv:
        return None, {"status": "error", "message": f"Serveur introuvable : {server_id}"}
    if expected_game and srv.get("game", "minecraft") != expected_game:
        label = "ARK" if srv.get("game") == "ark" else "Minecraft"
        return None, {"status": "error", "message": f"Ce serveur est un serveur {label}."}
    return srv, None


def _run_rcon(srv: dict, command: str, timeout: int = 5):
    with MCRcon(srv["rcon_host"], srv["rcon_pass"], port=srv["rcon_port"], timeout=timeout) as mcr:
        return mcr.command(command)


def _send_command(server_id: str, command: str, game: str):
    srv, error = _resolve_server(server_id, game)
    if error:
        return error

    if len(command) > 1000 or "\r" in command or "\n" in command:
        return {"status": "error", "message": "Commande invalide."}

    try:
        return {"status": "ok", "response": _run_rcon(srv, command)}
    except Exception as e:
        return {"status": "error", "message": f"Échec RCON ({srv['name']}): {str(e)}"}


@app.post("/api/mc/command")
def send_command(payload: CommandPayload):
    return _send_command(payload.server_id, payload.command, "minecraft")


@app.post("/api/ark/command")
def send_ark_command(payload: CommandPayload):
    return _send_command(payload.server_id, payload.command, "ark")


CREATE_NEW_CONSOLE = 0x00000010


def _start_ark(srv: dict, target_dir: str):
    exec_type, target_file, work_dir = find_ark_launcher(target_dir)
    if not exec_type:
        return {
            "status": "error",
            "message": "Aucun lanceur ARK trouvé : ajoute un start_ark.bat, ou installe l'exécutable dans ShooterGame/Binaries/Win64.",
        }

    try:
        if exec_type == "bat":
            subprocess.Popen(
                ["cmd.exe", "/d", "/c", target_file],
                cwd=work_dir,
                creationflags=CREATE_NEW_CONSOLE
            )
        else:
            is_asa = os.path.basename(target_file).lower() == "arkascendedserver.exe"
            map_name = srv.get("map") or ("TheIsland_WP" if is_asa else "TheIsland")
            session = re.sub(r"[?&\"']", " ", srv.get("name") or "ARK").strip()[:64] or "ARK"
            url = (
                f"{map_name}?listen?SessionName={session}"
                f"?Port={int(srv.get('server_port') or 7777)}"
                f"?QueryPort={int(srv.get('query_port') or 27015)}"
                f"?RCONEnabled=True?RCONPort={int(srv.get('rcon_port') or 27020)}"
            )
            subprocess.Popen(
                [target_file, url, "-server", "-log", "-NoBattlEye"],
                cwd=work_dir,
                creationflags=CREATE_NEW_CONSOLE
            )

        return {
            "status": "ok",
            "message": f"Serveur ARK lancé : [{os.path.basename(target_file)}] dans {work_dir}"
        }
    except Exception as e:
        return {"status": "error", "message": f"Erreur système : {str(e)}"}


async def _start_server(server_id: str, game: str, requested_dir: str = ""):
    global servers_db
    with _servers_lock:
        servers_db = load_servers()

    srv, error = _resolve_server(server_id, game)
    if error:
        return error

    target_dir = os.path.abspath(srv.get("server_dir") or "")
    if not srv.get("server_dir") or not os.path.isdir(target_dir):
        return {"status": "error", "message": "Répertoire du serveur introuvable."}

    if requested_dir and os.path.abspath(requested_dir) != target_dir:
        return {"status": "error", "message": "Dossier non autorisé : utilise le serveur enregistré."}

    if server_process(target_dir, game) is not None:
        return {"status": "error", "message": f"{srv['name']} est déjà en cours d'exécution."}

    if game == "ark":
        return _start_ark(srv, target_dir)

    exec_type, target_file, work_dir = find_server_executable(target_dir)
    if not exec_type:
        return {"status": "error", "message": f"Aucun .bat ou .jar trouvé dans : {target_dir}"}

    ram_max = srv.get("ram_max", "4G")
    if not valid_ram(ram_max):
        ram_max = "4G"

    try:
        if exec_type == "bat":
            subprocess.Popen(
                ["cmd.exe", "/d", "/c", target_file],
                cwd=work_dir,
                creationflags=CREATE_NEW_CONSOLE
            )
        else:
            subprocess.Popen(
                ["java", f"-Xmx{ram_max}", "-jar", os.path.basename(target_file), "nogui"],
                cwd=work_dir,
                creationflags=CREATE_NEW_CONSOLE
            )

        return {
            "status": "ok",
            "message": f"Serveur lancé : [{os.path.basename(target_file)}] dans {work_dir}"
        }
    except Exception as e:
        return {"status": "error", "message": f"Erreur système : {str(e)}"}


@app.post("/api/mc/start")
async def start_server(payload: ServerStartPayload):
    return await _start_server(payload.server_id, "minecraft", payload.server_dir)


@app.post("/api/ark/start")
async def start_ark_server(payload: ServerStartPayload):
    return await _start_server(payload.server_id, "ark", payload.server_dir)


def _stop_server(server_id: str, game: str):
    srv, error = _resolve_server(server_id, game)
    if error:
        return error

    if game == "ark":
        try:
            res = _run_rcon(srv, "DoExit")
            return {"status": "ok", "response": f"Arrêt demandé (DoExit) : {res}"}
        except Exception as e:
            return {
                "status": "error",
                "message": f"Échec envoi DoExit via RCON : {str(e)}",
                "hint": "Vérifie que RCON est activé côté ARK, ou utilise l'arrêt d'urgence."
            }

    try:
        res = _run_rcon(srv, "stop")
        return {"status": "ok", "response": f"Arrêt demandé (/stop) : {res}"}
    except Exception as e:
        return {
            "status": "error",
            "message": f"Échec envoi /stop via RCON : {str(e)}",
            "hint": "Si le serveur est bloqué, utilise l'arrêt d'urgence (panic)."
        }


@app.post("/api/mc/stop")
def stop_server(payload: ServerActionPayload):
    return _stop_server(payload.server_id, "minecraft")


@app.post("/api/ark/stop")
def stop_ark_server(payload: ServerActionPayload):
    return _stop_server(payload.server_id, "ark")


@app.post("/api/mc/difficulty")
def set_difficulty(payload: DifficultyPayload):
    srv = next((s for s in servers_db if s["id"] == payload.server_id), None)
    if not srv:
        return {"status": "error", "message": "Serveur introuvable"}

    diff = payload.difficulty.lower().strip()
    if diff not in ("peaceful", "easy", "normal", "hard"):
        return {"status": "error", "message": "Difficulté invalide."}

    try:
        with MCRcon(srv["rcon_host"], srv["rcon_pass"], port=srv["rcon_port"], timeout=5) as mcr:
            res = mcr.command(f"difficulty {diff}")
            return {"status": "ok", "response": res}
    except Exception as e:
        return {"status": "error", "message": f"Erreur RCON : {str(e)}"}


def _panic_server(server_id: str, game: str):
    srv, error = _resolve_server(server_id, game)
    if error:
        return error

    try:
        if game == "ark":
            _run_rcon(srv, "SaveWorld")
            res = _run_rcon(srv, "DoExit")
        else:
            _run_rcon(srv, "save-all")
            res = _run_rcon(srv, "stop")
        return {"status": "ok", "response": f"Arrêt d'urgence exécuté : {res}"}
    except Exception as e:
        target_dir = srv.get("server_dir") or ""
        if target_dir and os.path.isdir(target_dir) and server_process(target_dir, game) is not None:
            kill_server_processes(target_dir)
            return {
                "status": "ok",
                "response": f"RCON indisponible ({str(e)}), process du serveur tué de force."
            }
        return {"status": "error", "message": f"Erreur panic : {str(e)}"}


@app.post("/api/mc/panic")
def panic_stop(payload: ServerActionPayload):
    return _panic_server(payload.server_id, "minecraft")


@app.post("/api/ark/panic")
def panic_stop_ark(payload: ServerActionPayload):
    return _panic_server(payload.server_id, "ark")


# --- 5. EXECUTION DE SCRIPTS TOOLBOX ---
def _script_description(script_path: str) -> str:
    """Extrait la première ligne utile de la docstring du script (sans l'exécuter)."""
    try:
        with open(script_path, "r", encoding="utf-8-sig") as f:
            source = f.read(262144)
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree) or ""
        for line in docstring.splitlines():
            line = line.strip()
            if line:
                return line[:240]
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        pass
    return ""


def _script_public_info(name: str, stat_result) -> dict:
    return {
        "name": name,
        "size": stat_result.st_size,
        "modified": datetime.fromtimestamp(stat_result.st_mtime).isoformat(timespec="seconds"),
        "description": _script_description(os.path.join(config.SCRIPTS_DIR, name)),
    }


@app.get("/api/scripts")
def list_scripts():
    scripts = []
    try:
        entries = list(os.scandir(config.SCRIPTS_DIR))
    except OSError as exc:
        return JSONResponse(
            {"status": "error", "message": f"Dossier scripts inaccessible : {exc}"},
            status_code=500,
        )

    for entry in entries:
        if not entry.name.lower().endswith(".py") or not entry.is_file():
            continue
        try:
            scripts.append(_script_public_info(entry.name, entry.stat()))
        except OSError:
            continue

    scripts.sort(key=lambda item: item["name"].lower())
    return {"status": "ok", "scripts": scripts}


async def _read_limited(stream, limit: int):
    """Lit un flux en ne conservant que les `limit` premiers octets."""
    chunks = []
    kept = 0
    truncated = False
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        if kept < limit:
            remaining = limit - kept
            chunks.append(chunk[:remaining])
            kept += min(len(chunk), remaining)
            if len(chunk) > remaining:
                truncated = True
        else:
            truncated = True
    return b"".join(chunks), truncated


@app.post("/api/scripts/run")
async def run_script(payload: ScriptPayload):
    raw_name = payload.script_name.strip()
    if (not raw_name or raw_name != os.path.basename(raw_name)
            or "/" in raw_name or "\\" in raw_name or "\x00" in raw_name
            or not raw_name.lower().endswith(".py")):
        return {"status": "error", "message": "Nom de script invalide. Seuls les fichiers .py du dossier scripts/ sont autorisés."}

    if len(payload.args) > SCRIPT_MAX_ARGS:
        return {"status": "error", "message": f"Trop d'arguments (maximum {SCRIPT_MAX_ARGS})."}
    for arg in payload.args:
        if "\x00" in arg:
            return {"status": "error", "message": "Un argument contient un caractère interdit."}
        if len(arg) > SCRIPT_MAX_ARG_LENGTH:
            return {"status": "error", "message": f"Un argument dépasse {SCRIPT_MAX_ARG_LENGTH} caractères."}
    if sum(len(arg) for arg in payload.args) > SCRIPT_MAX_TOTAL_ARGS_LENGTH:
        return {"status": "error", "message": f"Total des arguments trop long (maximum {SCRIPT_MAX_TOTAL_ARGS_LENGTH} caractères)."}

    script_path = os.path.join(config.SCRIPTS_DIR, raw_name)
    if not os.path.isfile(script_path):
        return {"status": "error", "message": f"Script {raw_name} introuvable."}

    python_bin = sys.executable if not getattr(sys, "frozen", False) else "python"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = await asyncio.create_subprocess_exec(
            python_bin, script_path, *payload.args,
            cwd=config.BASE_DIR,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
    except Exception as exc:
        return {"status": "error", "message": f"Erreur système: {exc}"}

    stdout_task = asyncio.create_task(_read_limited(proc.stdout, SCRIPT_MAX_OUTPUT_BYTES))
    stderr_task = asyncio.create_task(_read_limited(proc.stderr, SCRIPT_MAX_OUTPUT_BYTES))
    timed_out = False

    try:
        await asyncio.wait_for(proc.wait(), timeout=SCRIPT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
    except Exception as exc:
        for task in (stdout_task, stderr_task):
            task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        return {"status": "error", "message": f"Erreur système: {exc}"}

    try:
        stdout, stdout_truncated = await asyncio.wait_for(stdout_task, timeout=5)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        stdout_task.cancel()
        stdout, stdout_truncated = b"", True
    try:
        stderr, stderr_truncated = await asyncio.wait_for(stderr_task, timeout=5)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        stderr_task.cancel()
        stderr, stderr_truncated = b"", True
    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

    result = {
        "status": "timeout" if timed_out else "ok",
        "exit_code": proc.returncode if proc.returncode is not None else -1,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
    if timed_out:
        result["message"] = f"Script interrompu après {SCRIPT_TIMEOUT_SECONDS} secondes."
    else:
        result["success"] = proc.returncode == 0
    return result


# --- 6. DOSSIERS PROPOSÉS DANS LA TOOLBOX ---
def _panel_relative_path(path: str) -> str:
    """Retourne un chemin relatif au panel quand c'est possible."""
    try:
        relative = os.path.relpath(path, config.BASE_DIR)
    except ValueError:
        return os.path.abspath(path)
    if relative == ".":
        return "."
    if relative == ".." or relative.startswith(".." + os.sep):
        return os.path.abspath(path)
    return relative.replace(os.sep, "/")


@app.get("/api/fs/directories")
def list_directories():
    groups = [
        {"id": "worlds", "label": "Mondes Minecraft", "directories": []},
        {"id": "servers", "label": "Serveurs", "directories": []},
        {"id": "panel", "label": "Dossiers du panel", "directories": []},
    ]
    groups_by_id = {group["id"]: group for group in groups}
    seen = set()

    def add(group_id, path, label, kind, game="", server_id="", has_world=False):
        if not path:
            return
        absolute = os.path.abspath(path)
        if not os.path.isdir(absolute):
            return
        key = os.path.normcase(absolute)
        if key in seen:
            return
        seen.add(key)
        groups_by_id[group_id]["directories"].append({
            "path": _panel_relative_path(absolute),
            "absolute_path": absolute,
            "label": label,
            "kind": kind,
            "game": game,
            "server_id": server_id,
            "has_world": has_world,
        })

    add("panel", config.BASE_DIR, "Racine du panel", "panel")
    for folder, label in (
        ("servers", "Dossier des serveurs"),
        ("backups", "Sauvegardes"),
        ("logs", "Logs"),
        ("scripts", "Scripts"),
    ):
        add("panel", os.path.join(config.BASE_DIR, folder), label, "system")

    for srv in servers_db:
        server_dir = srv.get("server_dir") or ""
        if not server_dir or not os.path.isdir(server_dir):
            continue
        name = srv.get("name") or srv.get("id") or os.path.basename(server_dir)
        game = srv.get("game", "minecraft")
        world = os.path.join(server_dir, "world")
        has_world = game == "minecraft" and os.path.isdir(world)
        if has_world:
            add("worlds", world, f"{name} — monde", "world",
                game=game, server_id=srv.get("id", ""), has_world=True)
        add("servers", server_dir, f"{name} — serveur", "server",
            game=game, server_id=srv.get("id", ""), has_world=has_world)

    # Dossiers créés dans servers/ mais pas encore enregistrés dans servers.json.
    if os.path.isdir(SERVERS_DIR):
        try:
            entries = sorted(os.scandir(SERVERS_DIR), key=lambda e: e.name.lower())
        except OSError:
            entries = []
        for entry in entries:
            if not entry.is_dir():
                continue
            world = os.path.join(entry.path, "world")
            has_world = os.path.isdir(world)
            if has_world:
                add("worlds", world, f"{entry.name} — monde", "world", has_world=True)
            add("servers", entry.path, f"{entry.name} — serveur", "server", has_world=has_world)

    for group in groups:
        if group["id"] == "panel":
            group["directories"].sort(
                key=lambda item: (item["kind"] != "panel", item["label"].lower())
            )
        else:
            group["directories"].sort(key=lambda item: item["label"].lower())

    visible_groups = [group for group in groups if group["directories"]]
    return {
        "status": "ok",
        "base_dir": config.BASE_DIR,
        "total": sum(len(group["directories"]) for group in visible_groups),
        "groups": visible_groups,
    }


# --- 7. EXPORT DU LISTING DE DOSSIER (FICHIER TEXTE) ---
@app.get("/api/fs/export-listing")
def export_folder_listing(target_dir: str = "."):
    cleaned_dir = target_dir.strip(' "\'')

    if not cleaned_dir or cleaned_dir == ".":
        resolved_path = os.path.abspath(getattr(config, "BASE_DIR", os.getcwd()))
    elif os.path.isabs(cleaned_dir):
        resolved_path = os.path.abspath(cleaned_dir)
    else:
        base = getattr(config, "BASE_DIR", os.getcwd())
        resolved_path = os.path.abspath(os.path.join(base, cleaned_dir))

    if not os.path.exists(resolved_path) or not os.path.isdir(resolved_path):
        return PlainTextResponse(
            f"Erreur 404 : Dossier introuvable.\n\n"
            f"Chemin demande : {target_dir}\n"
            f"Chemin résolu  : {resolved_path}\n"
            f"BASE_DIR       : {getattr(config, 'BASE_DIR', 'Non defini')}\n",
            status_code=404
        )

    folder_name = os.path.basename(resolved_path) or "racine"
    lines = [
        "============================================================",
        f"  INVENTAIRE DU DOSSIER : {resolved_path}",
        f"  DATE : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "============================================================\n"
    ]

    total_files = 0
    total_dirs = 0
    truncated = False

    try:
        for root, dirs, files in os.walk(resolved_path):
            rel_root = os.path.relpath(root, resolved_path)
            depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
            indent = "    " * depth

            if rel_root != ".":
                dname = os.path.basename(root)
                lines.append(f"{indent}[DIR]  {dname}/")
                total_dirs += 1

            sub_indent = "    " * (depth + (0 if rel_root == "." else 1))
            for f in sorted(files):
                file_path = os.path.join(root, f)
                try:
                    size_bytes = os.path.getsize(file_path)
                    if size_bytes >= 1024 * 1024:
                        size_str = f"{size_bytes / (1024*1024):.2f} Mo"
                    elif size_bytes >= 1024:
                        size_str = f"{size_bytes / 1024:.1f} Ko"
                    else:
                        size_str = f"{size_bytes} o"
                except OSError:
                    size_str = "N/A"

                lines.append(f"{sub_indent}[FILE] {f} ({size_str})")
                total_files += 1

                if total_files + total_dirs >= MAX_LISTING_ENTRIES:
                    truncated = True
                    break

            if truncated:
                break

        lines.append("\n------------------------------------------------------------")
        if truncated:
            lines.append(f"INVENTAIRE TRONQUÉ à {MAX_LISTING_ENTRIES} entrées.")
        lines.append(f"TOTAL : {total_dirs} dossiers, {total_files} fichiers.")
        lines.append("============================================================")

    except PermissionError:
        lines.append("\n[Erreur : Accès refusé à certains sous-dossiers]")

    content = "\n".join(lines)
    filename = f"inventaire_{folder_name}.txt"

    return PlainTextResponse(
        content,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


# --- 8. WEBSOCKET TELEMETRIE ---
async def _watch_client_close(websocket: WebSocket):
    try:
        while True:
            await websocket.receive()
    except Exception:
        return


@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket, server_id: str = ""):
    if not ws_authorized(websocket):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    watcher = asyncio.create_task(_watch_client_close(websocket))
    mc_proc = None
    cpu_cores = psutil.cpu_count() or 1

    server_dir = ""
    game = "minecraft"
    if server_id:
        srv = next((s for s in servers_db if s["id"] == server_id), None)
        if srv:
            server_dir = srv.get("server_dir") or ""
            game = srv.get("game", "minecraft")

    try:
        while not watcher.done():
            machine_cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()

            mc_ram_mb = 0.0
            mc_cpu_percent = 0.0
            mc_running = False

            if mc_proc is not None:
                try:
                    if not mc_proc.is_running() or mc_proc.status() == psutil.STATUS_ZOMBIE:
                        mc_proc = None
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    mc_proc = None

            if mc_proc is None and server_dir:
                mc_proc = server_process(server_dir, game)

            if mc_proc is not None:
                try:
                    mc_running = True
                    mc_ram_mb = round(mc_proc.memory_info().rss / (1024 * 1024), 1)
                    raw_mc_cpu = mc_proc.cpu_percent(interval=None)
                    mc_cpu_percent = round(raw_mc_cpu / cpu_cores, 1)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    mc_proc = None
                    mc_running = False

            await websocket.send_json({
                "type": "telemetry",
                "server_id": server_id,
                "game": game,
                "cpu": machine_cpu,
                "mc_cpu": mc_cpu_percent,
                "ram_total_percent": ram.percent,
                "ram_used_gb": round(ram.used / (1024**3), 2),
                "ram_total_gb": round(ram.total / (1024**3), 2),
                "mc_ram_mb": mc_ram_mb,
                "mc_running": mc_running
            })
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        watcher.cancel()
        try:
            await watcher
        except Exception:
            pass


# --- 9. WEBSOCKET LOGS JVM ---
@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket, server_id: str = ""):
    if not ws_authorized(websocket):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    if server_id:
        active_log_sockets.setdefault(server_id, set()).add(websocket)

    srv = next((s for s in servers_db if s["id"] == server_id), None)
    target_log = None
    if srv and srv.get("server_dir"):
        candidates = server_log_paths(srv)
        target_log = next((p for p in candidates if os.path.exists(p)), candidates[0])

    watcher = asyncio.create_task(_watch_client_close(websocket))
    handle = None
    opened_inode = None
    waiting_announced = False

    try:
        while not watcher.done():
            if not target_log or not os.path.exists(target_log):
                if not waiting_announced:
                    await websocket.send_json(
                        {"type": "log", "line": "[GOAT] En attente du fichier de log du serveur..."}
                    )
                    waiting_announced = True
                await asyncio.sleep(1)
                continue

            if waiting_announced:
                await websocket.send_json(
                    {"type": "log", "line": "[GOAT] Fichier de log détecté, lecture en cours."}
                )
                waiting_announced = False

            stat_info = os.stat(target_log)
            inode = getattr(stat_info, "st_ino", None)
            rotated = handle is not None and inode and opened_inode and inode != opened_inode

            if handle is None or rotated or stat_info.st_size < handle.tell():
                if handle is not None:
                    handle.close()
                handle = open(target_log, "r", encoding="utf-8", errors="ignore")
                opened_inode = inode
                size = stat_info.st_size
                back = min(size, 64 * 1024)
                if back:
                    handle.seek(size - back)
                    handle.readline()
                tail = handle.read().splitlines()
                for line in tail[-30:]:
                    if line.strip():
                        await websocket.send_json({"type": "log", "line": line.rstrip()})
                continue

            line = handle.readline()
            if line:
                await websocket.send_json({"type": "log", "line": line.rstrip()})
                continue

            await asyncio.sleep(0.25)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        watcher.cancel()
        if handle is not None:
            handle.close()
        sockets = active_log_sockets.get(server_id)
        if sockets is not None:
            sockets.discard(websocket)
            if not sockets:
                active_log_sockets.pop(server_id, None)


# --- 10. ROUTES DIRECTES VERS LES PAGES HTML DU PANEL ---
@app.get("/dashboard.html")
def get_dashboard():
    return FileResponse(os.path.join(config.PUBLIC_DIR, "dashboard.html"))


@app.get("/minecraft.html")
def get_minecraft():
    return FileResponse(os.path.join(config.PUBLIC_DIR, "minecraft.html"))


@app.get("/ark.html")
def get_ark():
    return FileResponse(os.path.join(config.PUBLIC_DIR, "ark.html"))


@app.get("/toolbox.html")
def get_toolbox():
    return FileResponse(os.path.join(config.PUBLIC_DIR, "toolbox.html"))


@app.get("/guide.html")
def get_guide():
    return FileResponse(os.path.join(config.PUBLIC_DIR, "guide.html"))


# --- 11. ASSETS STATIQUES ---
os.makedirs(config.PUBLIC_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=config.PUBLIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    if getattr(sys, "frozen", False):
        uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT)
    else:
        uvicorn.run("main:app", host=config.SERVER_HOST, port=config.SERVER_PORT, reload=True)
