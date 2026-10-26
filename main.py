import asyncio
import os
import json
import re
import glob
import shutil
import stat
import psutil
import subprocess
import sys
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from mcrcon import MCRcon

import config
import mc_installer

app = FastAPI(title="Genius Open Admin Tool (GOAT) API", version="pre-alpha")

SERVERS_FILE = os.path.join(config.BASE_DIR, "servers.json")
SERVERS_DIR = os.path.join(config.BASE_DIR, "servers")
os.makedirs(SERVERS_DIR, exist_ok=True)

active_log_sockets = {}

def remove_readonly(func, path, excinfo):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass

def kill_server_processes(server_dir):
    abs_dir = os.path.abspath(server_dir).lower()
    for proc in psutil.process_iter(['pid', 'name', 'cwd']):
        try:
            cwd = (proc.info.get('cwd') or '').lower()
            if cwd and os.path.abspath(cwd).lower() == abs_dir:
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

def apply_ram_to_server_files(server_dir: str, ram_max: str):
    if not os.path.isdir(server_dir):
        return

    jvm_args_path = os.path.join(server_dir, "user_jvm_args.txt")
    if os.path.exists(jvm_args_path):
        try:
            with open(jvm_args_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "-Xmx" in content:
                content = re.sub(r"-Xmx\w+", f"-Xmx{ram_max}", content)
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
                new_bat = re.sub(r"-Xmx\w+", f"-Xmx{ram_max}", bat_content)
                with open(bat_file, "w", encoding="utf-8") as f:
                    f.write(new_bat)
        except Exception:
            pass

def find_server_executable(target_dir):
    if not os.path.isdir(target_dir):
        return None, None, None

    priority_bats = ["run.bat", "start.bat", "launch.bat", "server.bat"]
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

def load_servers():
    servers = []
    if os.path.exists(SERVERS_FILE):
        try:
            with open(SERVERS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for s in saved:
                    s_dir = os.path.abspath(s.get("server_dir", ""))
                    if os.path.isdir(s_dir):
                        if "ram_max" not in s:
                            s["ram_max"] = "4G"
                        servers.append(s)
        except Exception:
            servers = []

    if os.path.exists(SERVERS_DIR):
        for entry in os.scandir(SERVERS_DIR):
            if entry.is_dir():
                srv_id = f"srv-{entry.name}"
                existing = next((s for s in servers if s.get("id") == srv_id), None)
                if not existing:
                    used_rcon = [s.get("rcon_port", 25575) for s in servers]
                    next_rcon = 25575
                    while next_rcon in used_rcon:
                        next_rcon += 1

                    servers.append({
                        "id": srv_id,
                        "name": entry.name.replace("_", " ").capitalize(),
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
    with open(SERVERS_FILE, "w", encoding="utf-8") as f:
        json.dump(servers, f, indent=4)

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
    servers_db = load_servers()
    return servers_db

@app.post("/api/mc/servers/ram")
def update_server_ram(payload: RamUpdatePayload):
    global servers_db
    srv = next((s for s in servers_db if s["id"] == payload.server_id), None)
    if not srv:
        return {"status": "error", "message": "Serveur introuvable."}

    srv["ram_max"] = payload.ram_max
    apply_ram_to_server_files(srv.get("server_dir", ""), payload.ram_max)
    save_servers(servers_db)

    return {"status": "ok", "message": f"RAM allouée fixée à {payload.ram_max} pour {srv['name']}."}

@app.post("/api/mc/servers/import")
def import_server(payload: ImportServerPayload):
    global servers_db
    target_dir = os.path.abspath(payload.server_dir.strip())

    if not os.path.isdir(target_dir):
        return {"status": "error", "message": f"Dossier introuvable sur le disque : {target_dir}"}

    exec_type, target_file, _ = find_server_executable(target_dir)
    if not exec_type:
        return {"status": "error", "message": f"Aucun fichier run.bat ou server.jar trouvé dans : {target_dir}"}

    folder_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', payload.name.lower())
    srv_id = f"srv-imp-{folder_name}"

    if any(s.get("id") == srv_id or os.path.abspath(s.get("server_dir", "")) == target_dir for s in servers_db):
        return {"status": "error", "message": "Ce dossier de serveur est déjà enregistré dans le panel."}

    used_rcon = [s.get("rcon_port", 25575) for s in servers_db]
    final_rcon = payload.rcon_port
    while final_rcon in used_rcon:
        final_rcon += 1

    apply_ram_to_server_files(target_dir, payload.ram_max)

    new_srv = {
        "id": srv_id,
        "name": payload.name,
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

    return {"status": "ok", "server": new_srv}

@app.delete("/api/mc/servers/{server_id}")
async def delete_server(server_id: str, delete_files: bool = False):
    global servers_db
    srv = next((s for s in servers_db if s["id"] == server_id), None)
    if not srv:
        return {"status": "error", "message": f"Serveur introuvable : {server_id}"}

    target_dir = os.path.abspath(srv.get("server_dir", ""))

    if server_id in active_log_sockets:
        try:
            ws = active_log_sockets.pop(server_id)
            await ws.close()
        except Exception:
            pass

    try:
        with MCRcon(srv["rcon_host"], srv["rcon_pass"], port=srv["rcon_port"], timeout=2) as mcr:
            mcr.command("stop")
    except Exception:
        pass

    if os.path.isdir(target_dir):
        kill_server_processes(target_dir)

    await asyncio.sleep(0.5)

    deleted_from_disk = False
    if delete_files and os.path.isdir(target_dir):
        base_root = os.path.abspath(config.BASE_DIR)
        if target_dir != base_root and not base_root.startswith(target_dir):
            try:
                shutil.rmtree(target_dir, onerror=remove_readonly)
                deleted_from_disk = True
            except Exception as e:
                return {"status": "error", "message": f"Erreur suppression disque : {str(e)}"}

    servers_db = [s for s in servers_db if s["id"] != server_id]
    save_servers(servers_db)

    detail = " et fichiers supprimés du disque." if deleted_from_disk else " (conservé sur le disque)."
    return {"status": "ok", "message": f"Serveur {srv['name']} retiré{detail}"}

# --- 2. ÉDITION DE SERVER.PROPERTIES ---
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

# --- 3. TÉLÉCHARGEMENT & VERSIONS DISTANTES ---
@app.get("/api/mc/remote-versions")
async def get_remote_versions(loader: str):
    versions = await mc_installer.get_loader_versions(loader)
    return {"loader": loader, "versions": versions}

@app.post("/api/mc/servers/install")
async def install_server(payload: AutoInstallPayload):
    folder_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', payload.name.lower())
    server_path = os.path.abspath(os.path.join(SERVERS_DIR, folder_name))
    
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
            "id": f"srv-{folder_name}",
            "name": payload.name,
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
        servers_db.append(new_srv)
        save_servers(servers_db)

        return {"status": "ok", "server": new_srv}
    except Exception as e:
        return {"status": "error", "message": f"Échec création : {str(e)}"}

# --- 4. COMMANDES RCON & PROCESSUS ---
@app.post("/api/mc/command")
def send_command(payload: CommandPayload):
    srv = next((s for s in servers_db if s["id"] == payload.server_id), None)
    if not srv:
        return {"status": "error", "message": f"Serveur introuvable : {payload.server_id}"}

    try:
        with MCRcon(srv["rcon_host"], srv["rcon_pass"], port=srv["rcon_port"], timeout=5) as mcr:
            response = mcr.command(payload.command)
            return {"status": "ok", "response": response}
    except Exception as e:
        return {"status": "error", "message": f"Échec RCON ({srv['name']}): {str(e)}"}

@app.post("/api/mc/start")
async def start_server(payload: ServerStartPayload):
    global servers_db
    servers_db = load_servers()
    srv = next((s for s in servers_db if s["id"] == payload.server_id), None)

    target_dir = ""
    if payload.server_dir and os.path.isdir(payload.server_dir):
        target_dir = os.path.abspath(payload.server_dir)
    elif srv and srv.get("server_dir") and os.path.isdir(srv["server_dir"]):
        target_dir = os.path.abspath(srv["server_dir"])

    if not target_dir or not os.path.isdir(target_dir):
        return {"status": "error", "message": "Répertoire du serveur introuvable."}

    exec_type, target_file, work_dir = find_server_executable(target_dir)

    if not exec_type:
        return {"status": "error", "message": f"Aucun .bat ou .jar trouvé dans : {target_dir}"}

    ram_max = srv.get("ram_max", "4G") if srv else "4G"

    try:
        CREATE_NEW_CONSOLE = 0x00000010
        if exec_type == "bat":
            subprocess.Popen(
                ["cmd.exe", "/c", target_file],
                cwd=work_dir,
                creationflags=CREATE_NEW_CONSOLE
            )
        else:
            subprocess.Popen(
                ["java", "-Xms2G", f"-Xmx{ram_max}", "-jar", target_file, "nogui"],
                cwd=work_dir,
                creationflags=CREATE_NEW_CONSOLE
            )

        if srv:
            srv["status"] = "online"
            save_servers(servers_db)

        return {
            "status": "ok",
            "message": f"Serveur lancé : [{os.path.basename(target_file)}] dans {work_dir}"
        }
    except Exception as e:
        return {"status": "error", "message": f"Erreur système : {str(e)}"}

@app.post("/api/mc/stop")
def stop_server(payload: ServerActionPayload):
    global servers_db
    srv = next((s for s in servers_db if s["id"] == payload.server_id), None)
    if not srv:
        return {"status": "error", "message": "Serveur introuvable"}

    try:
        with MCRcon(srv["rcon_host"], srv["rcon_pass"], port=srv["rcon_port"], timeout=5) as mcr:
            res = mcr.command("stop")
            srv["status"] = "stopped"
            save_servers(servers_db)
            return {"status": "ok", "response": f"Arrêt demandé (/stop) : {res}"}
    except Exception as e:
        return {"status": "error", "message": f"Échec envoi /stop via RCON : {str(e)}"}

@app.post("/api/mc/difficulty")
def set_difficulty(payload: DifficultyPayload):
    srv = next((s for s in servers_db if s["id"] == payload.server_id), None)
    if not srv:
        return {"status": "error", "message": "Serveur introuvable"}

    diff = payload.difficulty.lower().strip()
    try:
        with MCRcon(srv["rcon_host"], srv["rcon_pass"], port=srv["rcon_port"], timeout=5) as mcr:
            res = mcr.command(f"difficulty {diff}")
            return {"status": "ok", "response": res}
    except Exception as e:
        return {"status": "error", "message": f"Erreur RCON : {str(e)}"}

@app.post("/api/mc/panic")
def panic_stop(payload: ServerActionPayload):
    global servers_db
    srv = next((s for s in servers_db if s["id"] == payload.server_id), None)
    if not srv:
        return {"status": "error", "message": f"Serveur introuvable : {payload.server_id}"}

    try:
        with MCRcon(srv["rcon_host"], srv["rcon_pass"], port=srv["rcon_port"], timeout=5) as mcr:
            mcr.command("save-all")
            res = mcr.command("stop")
            srv["status"] = "stopped"
            save_servers(servers_db)
            return {"status": "ok", "response": f"Arrêt d'urgence exécuté : {res}"}
    except Exception as e:
        return {"status": "error", "message": f"Erreur panic : {str(e)}"}

# --- 5. EXÉCUTION DE SCRIPTS TOOLBOX ---
@app.post("/api/scripts/run")
async def run_script(payload: ScriptPayload):
    safe_name = os.path.basename(payload.script_name)
    script_path = os.path.join(config.SCRIPTS_DIR, safe_name)

    if not os.path.exists(script_path):
        return {"status": "error", "message": f"Script {safe_name} introuvable."}

    python_bin = sys.executable if not getattr(sys, "frozen", False) else "python"

    try:
        proc = await asyncio.create_subprocess_exec(
            python_bin, script_path, *payload.args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        return {
            "status": "ok",
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace")
        }
    except Exception as e:
        return {"status": "error", "message": f"Erreur système: {str(e)}"}

# --- 6. EXPORT DU LISTING DE DOSSIER (FICHIER TEXTE) ---
@app.get("/api/fs/export-listing")
def export_folder_listing(target_dir: str = "."):
    # Nettoyage des guillemets et espaces éventuels
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
            f"Chemin resolu  : {resolved_path}\n"
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

        lines.append("\n------------------------------------------------------------")
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
# --- 7. WEBSOCKET TÉLÉMÉTRIE ---
@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket):
    await websocket.accept()
    mc_proc = None
    cpu_cores = psutil.cpu_count() or 1

    try:
        while True:
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

            if mc_proc is None:
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        name = (proc.info['name'] or '').lower()
                        cmdline = " ".join(proc.info['cmdline'] or []).lower()
                        if 'java' in name and ('minecraft' in cmdline or 'forge' in cmdline or 'server.jar' in cmdline or 'neoforge' in cmdline):
                            mc_proc = psutil.Process(proc.info['pid'])
                            mc_proc.cpu_percent(interval=None)
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

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

# --- 8. WEBSOCKET LOGS JVM ---
@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket, server_id: str = ""):
    await websocket.accept()
    if server_id:
        active_log_sockets[server_id] = websocket

    global servers_db
    servers_db = load_servers()
    srv = next((s for s in servers_db if s["id"] == server_id), None)

    target_log = None
    if srv and srv.get("server_dir"):
        candidate = os.path.join(srv["server_dir"], "logs", "latest.log")
        if os.path.exists(candidate):
            target_log = candidate

    try:
        if not target_log or not os.path.exists(target_log):
            await websocket.send_json({"type": "log", "line": "[GOAT] En attente de l'activité du serveur..."})
            while True:
                await asyncio.sleep(1)

        with open(target_log, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            for line in lines[-30:]:
                if line.strip():
                    await websocket.send_json({"type": "log", "line": line.rstrip()})

            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if line:
                    await websocket.send_json({"type": "log", "line": line.rstrip()})
                else:
                    await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        pass
    finally:
        active_log_sockets.pop(server_id, None)

# --- 9. ROUTES DIRECTES VERS LES PAGES HTML DU PANEL ---
@app.get("/dashboard.html")
def get_dashboard():
    return FileResponse(os.path.join(config.PUBLIC_DIR, "dashboard.html"))

@app.get("/minecraft.html")
def get_minecraft():
    return FileResponse(os.path.join(config.PUBLIC_DIR, "minecraft.html"))

@app.get("/toolbox.html")
def get_toolbox():
    return FileResponse(os.path.join(config.PUBLIC_DIR, "toolbox.html"))

@app.get("/guide.html")
def get_guide():
    return FileResponse(os.path.join(config.PUBLIC_DIR, "guide.html"))

# --- 10. ASSETS STATIQUES ---
os.makedirs(config.PUBLIC_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=config.PUBLIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    
    if getattr(sys, "frozen", False):
        uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT)
    else:
        uvicorn.run("main:app", host=config.SERVER_HOST, port=config.SERVER_PORT, reload=True)