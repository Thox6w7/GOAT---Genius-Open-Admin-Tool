"""Création de serveurs ARK via SteamCMD et l'API Steam.

Aucun fichier de map n'est stocké par le panel : les cartes font partie du
téléchargement SteamCMD (app de base, plus le DLC de la map pour Survival
Ascended). Le panel ne conserve que des identifiants de maps.
"""

import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from datetime import datetime

import httpx

import config

STEAMCMD_ZIP_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAMCMD_DIR = os.path.join(config.BASE_DIR, "tools", "steamcmd")
INSTALL_LOG_DIR = os.path.join(config.BASE_DIR, "logs")

SERVER_INSTALL_TIMEOUT = int(os.getenv("GOAT_ARK_TIMEOUT", str(6 * 3600)))
STEAMCMD_BOOTSTRAP_TIMEOUT = int(os.getenv("GOAT_ARK_STEAMCMD_TIMEOUT", "900"))
MAX_LOG_LINES = 400
# Marqueur déposé pendant l'installation : le scanner de dossiers l'ignore.
INSTALL_MARKER = ".goat-installing"

EDITIONS = {
    "ase": {
        "label": "ARK: Survival Evolved",
        "app_id": 376030,
        "store_app_id": 346110,
        "exe": ("ShooterGame", "Binaries", "Win64", "ShooterGameServer.exe"),
        "maps_in_base": True,
        "default_map": "TheIsland",
        "maps": [
            ("TheIsland", "The Island"),
            ("ScorchedEarth", "Scorched Earth"),
            ("TheCenter", "The Center"),
            ("Ragnarok", "Ragnarok"),
            ("Aberration", "Aberration"),
            ("Extinction", "Extinction"),
            ("Valguero", "Valguero"),
            ("CrystalIsles", "Crystal Isles"),
            ("Genesis", "Genesis: Part 1"),
            ("Gen2", "Genesis: Part 2"),
            ("LostIsland", "Lost Island"),
            ("Fjordur", "Fjordur"),
        ],
    },
    "asa": {
        "label": "ARK: Survival Ascended",
        "app_id": 2430930,
        "store_app_id": 2399830,
        "exe": ("ShooterGame", "Binaries", "Win64", "ArkAscendedServer.exe"),
        "maps_in_base": False,
        "default_map": "TheIsland_WP",
        "maps": [
            ("TheIsland_WP", "The Island"),
            ("ScorchedEarth_WP", "Scorched Earth"),
            ("TheCenter_WP", "The Center"),
            ("Aberration_WP", "Aberration"),
            ("Extinction_WP", "Extinction"),
            ("Ragnarok_WP", "Ragnarok"),
            ("Valguero_WP", "Valguero"),
            ("CrystalIsles_WP", "Crystal Isles"),
            ("LostIsland_WP", "Lost Island"),
            ("Fjordur_WP", "Fjordur"),
        ],
    },
}

PROGRESS_RE = re.compile(r"progress:\s*([0-9]+(?:\.[0-9]+)?)")
SUCCESS_RE = re.compile(r"Success!\s+App\s+'(\d+)'\s+fully installed", re.IGNORECASE)

_jobs = {}
_jobs_lock = threading.Lock()
_active_job_id = None

_details_cache = {}
_details_lock = threading.Lock()
DETAILS_TTL = 1800


# --- 1. OUTILS STEAM ---
def steamcmd_candidates():
    env = os.getenv("GOAT_STEAMCMD")
    if env:
        yield env
    yield os.path.join(STEAMCMD_DIR, "steamcmd.exe")
    found = shutil.which("steamcmd.exe") or shutil.which("steamcmd")
    if found:
        yield found
    for base in (
        r"C:\steamcmd\steamcmd.exe",
        r"C:\Program Files\Steam\steamcmd.exe",
        r"C:\Program Files (x86)\Steam\steamcmd.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "steamcmd", "steamcmd.exe"),
        os.path.join(os.environ.get("USERPROFILE", ""), "steamcmd", "steamcmd.exe"),
    ):
        if base:
            yield base


def steamcmd_path():
    for candidate in steamcmd_candidates():
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def steamcmd_status():
    path = steamcmd_path()
    return {
        "installed": bool(path),
        "path": path or "",
        "managed_dir": STEAMCMD_DIR,
    }


async def fetch_app_details(app_id: int):
    """Métadonnées d'une app Steam (nom + DLC). Résultat mis en cache."""
    key = int(app_id)
    now = time.time()
    with _details_lock:
        cached = _details_cache.get(key)
        if cached and now - cached[0] < DETAILS_TTL:
            return cached[1]

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        res = await client.get(
            STEAM_APPDETAILS_URL,
            params={"appids": str(key), "l": "french", "cc": "FR"},
            headers={"User-Agent": "GOAT-panel"},
        )
        res.raise_for_status()
        payload = res.json().get(str(key)) or {}
        if not payload.get("success"):
            return None
        data = payload.get("data") or {}
        details = {
            "name": data.get("name") or "",
            "dlc": [int(d) for d in (data.get("dlc") or []) if str(d).isdigit()],
        }

    with _details_lock:
        _details_cache[key] = (now, details)
    return details


async def resolve_map_apps(edition_key: str, map_id: str):
    """Retourne (app_ids, libellés, avertissements) à installer pour une map.

    Les maps ne sont jamais stockées par le panel : elles arrivent avec l'app
    serveur téléchargée par SteamCMD.
    """
    edition = EDITIONS[edition_key]
    base_app = edition["app_id"]
    base_label = f"{edition['label']} (app {base_app})"
    warnings = []

    if edition_key == "asa" and map_id != edition["default_map"]:
        warnings.append(
            "Les maps ASA supplémentaires sont distribuées avec l'app serveur 2430930. "
            "Si SteamCMD renvoie « Access Denied » sur un dépôt, la map est un DLC payant : "
            "connecte SteamCMD avec un compte Steam qui la possède."
        )

    return [base_app], [base_label], warnings


async def editions_payload():
    """Éditions ARK, maps disponibles et état de SteamCMD."""
    result = []
    for key, edition in EDITIONS.items():
        entry = {
            "key": key,
            "label": edition["label"],
            "app_id": edition["app_id"],
            "default_map": edition["default_map"],
            "maps_in_base": edition["maps_in_base"],
            "maps": [{"id": m, "label": lbl} for m, lbl in edition["maps"]],
            "api_ok": False,
            "message": "",
        }
        try:
            details = await fetch_app_details(edition["store_app_id"])
            if details and details.get("name"):
                entry["label"] = details["name"]
            entry["api_ok"] = True
            entry["message"] = (
                "Toutes les maps officielles arrivent avec l'app serveur SteamCMD."
                if edition["maps_in_base"]
                else "Les maps arrivent avec l'app serveur SteamCMD ; le panel ne stocke aucun fichier de map."
            )
        except Exception as exc:
            entry["message"] = (
                f"API Steam injoignable ({exc}). L'installation utilisera l'app serveur {edition['app_id']}."
            )
        result.append(entry)

    return {"steamcmd": steamcmd_status(), "editions": result}


# --- 2. SUIVI DES JOBS ---
def _now():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def _new_job(kind: str, **extra):
    job_id = uuid.uuid4().hex[:12]
    os.makedirs(INSTALL_LOG_DIR, exist_ok=True)
    log_file = os.path.join(INSTALL_LOG_DIR, f"ark-{kind}-{job_id}.log")
    job = {
        "id": job_id,
        "kind": kind,
        "state": "queued",
        "progress": 0,
        "lines": [],
        "log_file": log_file,
        "started_at": _now(),
        "finished_at": "",
        "error": "",
        "app_total": 1,
        "app_index": 0,
        "stage": "En attente...",
        "server_id": "",
        "name": "",
        "edition": "",
        "map": "",
        "server_dir": "",
        "app_ids": [],
        "app_labels": [],
        "warnings": [],
    }
    job.update(extra)
    job["app_total"] = max(1, len(job["app_ids"]) or 1)

    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"[{_now()}] Job {job_id} ({kind})\n")

    with _jobs_lock:
        _jobs[job_id] = job
    return job


def _append(job, text):
    cleaned = (text or "").rstrip()
    if not cleaned:
        return
    job["lines"].append(cleaned)
    if len(job["lines"]) > MAX_LOG_LINES:
        del job["lines"][: len(job["lines"]) - MAX_LOG_LINES]
    try:
        with open(job["log_file"], "a", encoding="utf-8", errors="replace") as f:
            f.write(cleaned + "\n")
    except OSError:
        pass


def _update_progress(job, line):
    label = ""
    if job["app_ids"]:
        idx = min(job["app_index"], len(job["app_labels"]) - 1)
        label = job["app_labels"][idx] if job["app_labels"] else ""
    total = max(1, job["app_total"])

    match = PROGRESS_RE.search(line)
    if match:
        try:
            pct = max(0.0, min(100.0, float(match.group(1))))
        except ValueError:
            pct = 0.0
        job["progress"] = int(((job["app_index"] + pct / 100.0) / total) * 100)
        job["stage"] = f"{label} — {pct:.1f} %" if label else f"Téléchargement — {pct:.1f} %"
        return

    success = SUCCESS_RE.search(line)
    if success:
        job["app_index"] = min(total, job["app_index"] + 1)
        job["progress"] = int((job["app_index"] / total) * 100)
        job["stage"] = f"Installé : {label or success.group(1)}"


def _run_process(job, cmd, cwd, timeout):
    _append(job, "[CMD] " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    env = os.environ.copy()
    env.setdefault("SteamAppId", "")
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    started = time.time()
    try:
        for raw in proc.stdout:
            _append(job, raw)
            _update_progress(job, raw.rstrip())
            if timeout and time.time() - started > timeout:
                proc.kill()
                raise TimeoutError("délai maximal dépassé")
        return proc.wait()
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass


def _finish(job, state, error=""):
    job["state"] = state
    job["finished_at"] = _now()
    job["error"] = error
    if state == "done":
        job["progress"] = 100
        job["stage"] = "Terminé"
        _append(job, f"[{_now()}] Installation terminée.")
    else:
        job["stage"] = "Échec"
        _append(job, f"[{_now()}] ERREUR : {error}")


def _bootstrap_steamcmd(job):
    exe = steamcmd_path()
    if exe:
        return exe

    job["stage"] = "Téléchargement de SteamCMD..."
    os.makedirs(STEAMCMD_DIR, exist_ok=True)
    archive = os.path.join(STEAMCMD_DIR, "steamcmd.zip")
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        with client.stream("GET", STEAMCMD_ZIP_URL) as res:
            res.raise_for_status()
            with open(archive, "wb") as f:
                for chunk in res.iter_bytes():
                    f.write(chunk)
    _append(job, f"[INFO] SteamCMD téléchargé ({os.path.getsize(archive)} octets).")

    with zipfile.ZipFile(archive) as zf:
        zf.extractall(STEAMCMD_DIR)
    os.remove(archive)

    exe = os.path.join(STEAMCMD_DIR, "steamcmd.exe")
    if not os.path.isfile(exe):
        raise RuntimeError("steamcmd.exe absent de l'archive téléchargée.")

    job["stage"] = "Mise à jour de SteamCMD..."
    job["app_total"] = 1
    job["app_index"] = 0
    code = _run_process(job, [exe, "+quit"], STEAMCMD_DIR, STEAMCMD_BOOTSTRAP_TIMEOUT)
    if code != 0:
        raise RuntimeError(f"SteamCMD a renvoyé le code {code}.")
    return exe


def _write_configs(server_dir, edition_key, params):
    edition = EDITIONS[edition_key]
    cfg_dir = os.path.join(server_dir, "ShooterGame", "Saved", "Config", "WindowsServer")
    os.makedirs(cfg_dir, exist_ok=True)

    settings = f"""[ServerSettings]
SessionName={params['name']}
ServerAdminPassword={params.get('admin_pass', '')}
ServerPassword=
RCONEnabled=True
RCONPort={int(params['rcon_port'])}
Port={int(params['server_port'])}
QueryPort={int(params['query_port'])}
MaxPlayers={int(params.get('max_players', 70))}
Map={params['map']}
DifficultyOffset=1.0
ServerPVE=False
ServerCrosshair=True
NoBattlEye=True
"""
    with open(os.path.join(cfg_dir, "GameUserSettings.ini"), "w", encoding="utf-8") as f:
        f.write(settings)

    game_ini = os.path.join(cfg_dir, "Game.ini")
    if not os.path.exists(game_ini):
        with open(game_ini, "w", encoding="utf-8") as f:
            f.write("[/script/shootergame.shootergamemode]\n")

    _append_log = os.path.join(server_dir, "GOAT-ARK.txt")
    with open(_append_log, "w", encoding="utf-8") as f:
        f.write(
            f"Serveur ARK créé par GOAT le {_now()}\n"
            f"Edition   : {edition['label']} (app {edition['app_id']})\n"
            f"Map       : {params['map']}\n"
            f"Jeu       : {int(params['server_port'])}\n"
            f"Query     : {int(params['query_port'])}\n"
            f"RCON      : {int(params['rcon_port'])}\n"
            f"Config    : {cfg_dir}\n"
        )


def _worker(job, params, on_success):
    global _active_job_id
    try:
        job["state"] = "running"
        job["stage"] = "Préparation de SteamCMD..."
        exe = steamcmd_path() or _bootstrap_steamcmd(job)
        if not exe:
            raise RuntimeError("SteamCMD introuvable.")

        server_dir = params["server_dir"]
        os.makedirs(server_dir, exist_ok=True)
        marker = os.path.join(server_dir, INSTALL_MARKER)
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(f"Installation GOAT en cours depuis {_now()}\n")
        except OSError:
            pass

        cmd = [exe, "+force_install_dir", server_dir, "+login", "anonymous"]
        for app_id in params["app_ids"]:
            cmd += ["+app_update", str(app_id), "validate"]
        cmd.append("+quit")

        job["stage"] = "Téléchargement du serveur ARK (plusieurs Go)..."
        code = _run_process(job, cmd, os.path.dirname(exe), SERVER_INSTALL_TIMEOUT)
        if code != 0:
            raise RuntimeError(f"SteamCMD a renvoyé le code {code}. Consulte le journal d'installation.")

        edition = EDITIONS[params["edition"]]
        exe_server = os.path.join(server_dir, *edition["exe"])
        if not os.path.isfile(exe_server):
            raise RuntimeError(
                f"L'exécutable du serveur est absent après installation : {exe_server}"
            )

        job["stage"] = "Écriture de la configuration..."
        _write_configs(server_dir, params["edition"], params)

        try:
            os.remove(os.path.join(server_dir, INSTALL_MARKER))
        except OSError:
            pass

        if on_success:
            try:
                on_success(job)
            except Exception as exc:
                _append(job, f"[ERREUR] Enregistrement du serveur : {exc}")
                raise

        _finish(job, "done")
    except Exception as exc:
        _finish(job, "error", str(exc))
    finally:
        with _jobs_lock:
            _active_job_id = None


def _steamcmd_worker(job):
    global _active_job_id
    try:
        job["state"] = "running"
        _bootstrap_steamcmd(job)
        _finish(job, "done")
    except Exception as exc:
        _finish(job, "error", str(exc))
    finally:
        with _jobs_lock:
            _active_job_id = None


def _busy():
    with _jobs_lock:
        if not _active_job_id:
            return None
        job = _jobs.get(_active_job_id)
        if job and job["state"] in ("queued", "running"):
            return job
    return None


def start_server_job(params, on_success=None):
    """Démarre l'installation d'un serveur ARK en tâche de fond."""
    global _active_job_id
    busy = _busy()
    if busy:
        return None, f"Une installation est déjà en cours ({busy['kind']} {busy['id']}). Réessaie à la fin."

    job = _new_job(
        "server",
        name=params["name"],
        edition=params["edition"],
        map=params["map"],
        server_id=params["server_id"],
        server_dir=params["server_dir"],
        app_ids=params["app_ids"],
        app_labels=params["app_labels"],
        warnings=params.get("warnings", []),
    )
    for warning in job["warnings"]:
        _append(job, f"[AVERTISSEMENT] {warning}")

    with _jobs_lock:
        _active_job_id = job["id"]

    thread = threading.Thread(target=_worker, args=(job, params, on_success), daemon=True)
    thread.start()
    return job, ""


def start_steamcmd_job():
    global _active_job_id
    busy = _busy()
    if busy:
        return None, f"Une installation est déjà en cours ({busy['kind']} {busy['id']})."

    job = _new_job("steamcmd")
    with _jobs_lock:
        _active_job_id = job["id"]

    thread = threading.Thread(target=_steamcmd_worker, args=(job,), daemon=True)
    thread.start()
    return job, ""


def public_job(job):
    if not job:
        return None
    return {
        "id": job["id"],
        "kind": job["kind"],
        "state": job["state"],
        "progress": job["progress"],
        "stage": job["stage"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "error": job["error"],
        "server_id": job["server_id"],
        "name": job["name"],
        "edition": job["edition"],
        "map": job["map"],
        "server_dir": job["server_dir"],
        "app_ids": job["app_ids"],
        "app_labels": job["app_labels"],
        "warnings": job["warnings"],
        "log_file": job["log_file"],
        "lines": job["lines"][-120:],
    }


def get_status(job_id: str = ""):
    with _jobs_lock:
        if job_id:
            job = _jobs.get(job_id)
        else:
            job = _jobs.get(_active_job_id) if _active_job_id else None
            if job is None and _jobs:
                job = max(_jobs.values(), key=lambda j: j["started_at"])
    return public_job(job)
