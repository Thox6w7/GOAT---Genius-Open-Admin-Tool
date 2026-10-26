import os
import re
import httpx
import xml.etree.ElementTree as ET

CACHE_VERSIONS = {}

def parse_mc_version_tuple(v_str: str):
    clean = re.sub(r"[^\d\.]", "", v_str.split()[0])
    parts = []
    for part in clean.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)

async def fetch_vanilla_versions():
    url = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(url)
        data = res.json()
        releases = [v["id"] for v in data.get("versions", []) if v.get("type") == "release"]
        return releases

async def fetch_neoforge_versions():
    url = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(url)
        if res.status_code != 200:
            return []
        root = ET.fromstring(res.text)
        versioning = root.find("versioning")
        if versioning is None:
            return []
        versions_elem = versioning.find("versions")
        if versions_elem is None:
            return []
        all_versions = [v.text for v in versions_elem.findall("version") if v.text]
        all_versions.reverse()
        return all_versions

async def fetch_forge_versions():
    url = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(url)
        if res.status_code != 200:
            return []
        data = res.json()
        promos = data.get("promos", {})
        mc_map = {}
        for promo_key, forge_build in promos.items():
            if promo_key in ("latest", "recommended"):
                continue
            mc_ver, promo_type = promo_key.rsplit("-", 1)
            if mc_ver not in mc_map:
                mc_map[mc_ver] = {}
            mc_map[mc_ver][promo_type] = forge_build
        
        sorted_mc = sorted(mc_map.keys(), key=parse_mc_version_tuple, reverse=True)
        results = []
        for mc in sorted_mc:
            if "recommended" in mc_map[mc]:
                results.append(f"{mc} ({mc_map[mc]['recommended']})")
            elif "latest" in mc_map[mc]:
                results.append(f"{mc} ({mc_map[mc]['latest']} - latest)")
        return results

async def fetch_sponge_versions():
    url = "https://dl-api.spongepowered.org/v2/groups/org.spongepowered/artifacts/spongevanilla"
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(url)
        data = res.json()
        versions = list(data.get("assets", {}).keys())
        versions.reverse()
        return versions

async def get_loader_versions(loader: str):
    loader = loader.lower().strip()
    if loader in CACHE_VERSIONS:
        return CACHE_VERSIONS[loader]

    versions = []
    try:
        if loader == "vanilla":
            versions = await fetch_vanilla_versions()
        elif loader == "neoforge":
            versions = await fetch_neoforge_versions()
        elif loader == "forge":
            versions = await fetch_forge_versions()
        elif loader == "sponge":
            versions = await fetch_sponge_versions()
        if versions:
            CACHE_VERSIONS[loader] = versions
    except Exception as e:
        print(f"[ERREUR] Échec récupération versions {loader}: {e}")
        return []
    return versions

async def setup_server_files(server_dir: str, loader: str, version: str, rcon_port: int, server_port: int, rcon_pass: str, ram_max: str = "4G"):
    os.makedirs(server_dir, exist_ok=True)
    logs_dir = os.path.join(server_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    # 1. Validation EULA
    with open(os.path.join(server_dir, "eula.txt"), "w", encoding="utf-8") as f:
        f.write("eula=true\n")

    # 2. Configuration server.properties synchronisée
    props_path = os.path.join(server_dir, "server.properties")
    props_content = f"""enable-rcon=true
rcon.port={rcon_port}
rcon.password={rcon_pass}
server-port={server_port}
broadcast-rcon-to-ops=true
difficulty=easy
gamemode=survival
motd=Nexus Managed Server
"""
    with open(props_path, "w", encoding="utf-8") as f:
        f.write(props_content)

    # 3. Log initial
    initial_log = os.path.join(logs_dir, "latest.log")
    if not os.path.exists(initial_log):
        with open(initial_log, "w", encoding="utf-8") as f:
            f.write(f"[Nexus] Serveur {loader} {version} initialise sur port {server_port} (RCON: {rcon_port}) avec {ram_max} RAM.\n")

    # 4. Fichier user_jvm_args.txt pour Forge / NeoForge modernes
    user_jvm_path = os.path.join(server_dir, "user_jvm_args.txt")
    with open(user_jvm_path, "w", encoding="utf-8") as f:
        f.write(f"# Paramètres de mémoire gérés par Nexus Panel\n-Xms2G\n-Xmx{ram_max}\n")

    # 5. Téléchargement et génération du lanceur .bat
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        if loader == "vanilla":
            manifest_res = await client.get("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json")
            manifest = manifest_res.json()
            entry = next((v for v in manifest["versions"] if v["id"] == version), None)
            if not entry:
                raise ValueError(f"Version Vanilla {version} non trouvée.")
            
            pkg_res = await client.get(entry["url"])
            pkg_data = pkg_res.json()
            dl_url = pkg_data["downloads"]["server"]["url"]

            jar_dest = os.path.join(server_dir, "server.jar")
            async with client.stream("GET", dl_url) as stream:
                with open(jar_dest, "wb") as f:
                    async for chunk in stream.aiter_bytes():
                        f.write(chunk)

            with open(os.path.join(server_dir, "run.bat"), "w", encoding="utf-8") as f:
                f.write(f"@echo off\njava -Xms2G -Xmx{ram_max} -jar server.jar nogui\npause\n")

        elif loader == "neoforge":
            clean_version = version.strip()
            installer_url = f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{clean_version}/neoforge-{clean_version}-installer.jar"
            installer_path = os.path.join(server_dir, "neoforge-installer.jar")
            
            async with client.stream("GET", installer_url) as stream:
                if stream.status_code != 200:
                    raise ValueError(f"Impossible de télécharger l'installeur NeoForge {clean_version} (HTTP {stream.status_code})")
                with open(installer_path, "wb") as f:
                    async for chunk in stream.aiter_bytes():
                        f.write(chunk)

            with open(os.path.join(server_dir, "run.bat"), "w", encoding="utf-8") as f:
                f.write(f"""@echo off
if not exist "run.bat.installed" (
    echo [Nexus] Installation silencieuse de NeoForge en cours...
    java -jar neoforge-installer.jar --installServer
    type nul > "run.bat.installed"
)
if exist "run.bat" (
    call run.bat
) else (
    java -Xms2G -Xmx{ram_max} -jar server.jar nogui
)
pause
""")

        elif loader == "forge":
            match = re.search(r"^([\d\.]+)\s*\(([\d\.]+)", version.strip())
            if match:
                forge_full = f"{match.group(1)}-{match.group(2)}"
            else:
                forge_full = version.strip()

            forge_url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{forge_full}/forge-{forge_full}-installer.jar"
            installer_path = os.path.join(server_dir, "forge-installer.jar")
            
            async with client.stream("GET", forge_url) as stream:
                if stream.status_code != 200:
                    raise ValueError(f"Impossible de télécharger l'installeur Forge {forge_full} (HTTP {stream.status_code})")
                with open(installer_path, "wb") as f:
                    async for chunk in stream.aiter_bytes():
                        f.write(chunk)

            with open(os.path.join(server_dir, "run.bat"), "w", encoding="utf-8") as f:
                f.write(f"""@echo off
if not exist "forge-installed.txt" (
    echo [Nexus] Installation silencieuse de Forge en cours...
    java -jar forge-installer.jar --installServer
    type nul > "forge-installed.txt"
)
if exist "run.bat" (
    call run.bat
) else (
    java -Xms2G -Xmx{ram_max} -jar server.jar nogui
)
pause
""")

        elif loader == "sponge":
            sponge_url = f"https://repo.spongepowered.org/repository/maven-releases/org/spongepowered/spongevanilla/{version}/spongevanilla-{version}.jar"
            jar_dest = os.path.join(server_dir, "server.jar")
            async with client.stream("GET", sponge_url) as stream:
                with open(jar_dest, "wb") as f:
                    async for chunk in stream.aiter_bytes():
                        f.write(chunk)
                        
            with open(os.path.join(server_dir, "run.bat"), "w", encoding="utf-8") as f:
                f.write(f"@echo off\njava -Xms2G -Xmx{ram_max} -jar server.jar nogui\npause\n")