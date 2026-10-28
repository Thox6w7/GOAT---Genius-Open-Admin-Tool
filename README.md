# 🐐 Genius Open Admin Tool (GOAT)

![Version](https://img.shields.io/badge/version-Pre--Alpha-amber)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-emerald)

> **« Projet amateur visant à faciliter l'administration de serveurs Minecraft et à la rendre accessible. »**

GOAT est un outil local d'administration et de supervision système conçu pour gérer des instances Minecraft (Vanilla, Forge, NeoForge, Sponge) sans abonnement, sans création de compte et sans dépendance externe.

---

## 🚀 Fonctionnalités clés

- **Déploiement en 1 clic :** téléchargement automatisé des versions et chargeurs avec validation automatique de l'EULA.
- **Importation simple :** prise en charge des serveurs moddés existants via chemin absolu.
- **Console temps réel :** streaming des logs JVM via WebSocket et terminal de commandes RCON interactif.
- **Serveurs ARK :** création automatique de serveurs ARK: Survival Evolved via SteamCMD (sans stocker de map), import de serveurs existants, lancement par `.bat` ou exécutable, console RCON (`SaveWorld`, `DoExit`) et logs `ShooterGame.log` en direct.
- **Éditeur de configuration :** modification directe de `server.properties` avec recherche intégrée.
- **Supervision système :** métriques CPU/RAM de l'hôte vs processus Java en direct.
- **Python Toolbox :** exécution des scripts de `scripts/` depuis l'interface (stdout/stderr, code de sortie), inventaire de dossiers et sauvegardes horodatées du dossier `world`.
- **Accès protégé :** mot de passe unique, session signée, panel fermé par défaut au réseau local.

---

## 🛠️ Installation & lancement (développement)

```bash
# Cloner le dépôt
git clone https://github.com/TON_PSEUDO/GOAT.git
cd GOAT

# Créer et activer l'environnement virtuel
python -m venv .venv
.venv\Scripts\activate

# Installer les dépendances
pip install -r requirements.txt

# Lancer l'application en mode dev
python main.py
```

Le panel écoute sur `http://localhost:8000`. Au premier lancement, la page de connexion
propose de définir le mot de passe du panel (8 caractères minimum, stocké haché en
PBKDF2-SHA256 dans `auth.json`).

### Accès depuis le réseau local

```bat
:: SERVER_HOST n'est pas défini par défaut : écoute locale uniquement.
:: Pour ouvrir le panel au réseau, indique ici l'interface d'écoute à exposer.
set SERVER_HOST=<interface-reseau>
set SERVER_PORT=8000
python main.py
```

L'authentification reste obligatoire. La configuration initiale du mot de passe doit
être faite depuis la machine hôte (premier démarrage en local), puis le panel est
accessible depuis les autres appareils du réseau.

---

## 🔐 Sécurité

- Mot de passe haché (PBKDF2-HMAC-SHA256, 310 000 itérations) + sel aléatoire.
- Session sans état : cookie `HttpOnly` signé HMAC-SHA256, valable 7 jours.
- Limitation des tentatives de connexion (10 essais / 5 minutes / IP).
- Vérification d'origine sur les requêtes mutantes et les WebSockets (anti-CSRF).
- Les mots de passe RCON et admin ne sont jamais renvoyés par l'API :
  `GET /api/mc/servers` et `GET /api/ark/servers` les occultent (seuls
  `has_rcon_pass` et `has_admin_pass` sont exposés).
- Suppression disque limitée au dossier `servers/` : un serveur importé ailleurs est
  retiré du panel mais ses fichiers ne sont jamais effacés.
- `servers.json` est écrit de façon atomique (fichier temporaire + `os.replace`).

Fichiers sensibles, ignorés par git : `auth.json`, `secret.key`, `servers.json`,
`servers/`.

### Changer le mot de passe

```bash
curl -X POST http://localhost:8000/api/auth/password \
  -H "Content-Type: application/json" \
  -H "Cookie: goat_session=<ton cookie>" \
  -d "{\"current\":\"ancien\",\"new\":\"nouveau-mot-de-passe\"}"
```

Le changement invalide toutes les sessions existantes.

### Mot de passe oublié

Panel arrêté, supprime `auth.json` (et éventuellement `secret.key`) : le prochain
démarrage reproposera la configuration initiale. Aucune donnée de serveur n'est
touchée.

---

## 🦖 Serveurs ARK (Survival Evolved)

L'onglet **ARK** (`/ark.html`) gère ARK: Survival Evolved : création automatique via
SteamCMD, ou import d'une installation déjà présente sur le disque.

- **Création :** `POST /api/ark/servers/create` valide le nom, la version, la map et les
  ports (champs vides = attribution automatique), puis lance SteamCMD en arrière-plan
  (`+login anonymous +app_update 376030 validate`). Le panel ne stocke **aucune map** :
  les cartes sont téléchargées avec l'app serveur Steam `376030`. L'avancement se suit
  avec `GET /api/ark/install/status?job_id=...` et s'écrit dans
  `logs/ark-server-<job>.log`.
- **Versions :** deux choix à la création. **Pré-Aquatica (358.24)** est sélectionnée
  par défaut et mise en avant : elle utilise la branche SteamCMD `-beta preaquatica` et
  reste compatible avec la majorité des mods existants. **Dernière version (Aquatica)**
  installe la branche publique : l'update Aquatica a changé le chargement des assets et
  casse de nombreux mods, qui doivent être recuits.
- **SteamCMD :** `GET /api/ark/catalog` renvoie l'édition, les versions, les maps
  disponibles et l'état de SteamCMD. `POST /api/ark/steamcmd/install` le télécharge dans
  `tools/steamcmd/` (ignoré par git) si `steamcmd.exe` n'est pas déjà dans le `PATH`.
- **Pendant l'installation :** le dossier porte un marqueur `.goat-installing` et reste
  masqué dans la liste. À la fin, le serveur apparaît avec `GameUserSettings.ini` déjà
  écrit (session, ports, RCON, mot de passe admin, nombre de joueurs).
- **Import :** indique le dossier racine du serveur (celui qui contient `ShooterGame`,
  un lanceur `.bat` ou `ShooterGame/Binaries/Win64/ShooterGameServer.exe`).
- **Lancement :** priorité au premier `.bat` trouvé (`start_ark.bat`, `ark.bat`,
  `goat_ark.bat`, `run.bat`, `start.bat`, `launch.bat`, ou tout `.bat` contenant
  `ShooterGameServer`). À défaut, l'exécutable est lancé avec les
  arguments URL (`?listen?SessionName=...?Port=...`) et `-server -log -NoBattlEye`.
- **Détection du process :** par chemin de l'exécutable ARK dans le dossier du serveur
  (le cwd d'ARK est souvent `ShooterGame/Binaries/Win64`).
- **Logs :** `ShooterGame/Saved/Logs/ShooterGame.log` (repli sur
  `ShooterGameBackup.log`), diffusés dans la console du panel.
- **RCON :** active `RCONEnabled=True` et `RCONPort=<port>` dans le lanceur ARK, et
  utilise le même mot de passe que dans l'onglet ARK. L'arrêt propre envoie
  `SaveWorld` puis `DoExit` ; l'arrêt d'urgence fait de même puis tue le process si
  RCON est injoignable.
- **Découverte automatique :** un dossier ARK placé dans `servers/` est détecté au
  chargement (map `TheIsland`, ports 7777/27015/27020 par défaut).
- **Suppression :** comme pour Minecraft, les fichiers ne sont effacés que si le
  dossier se trouve dans `servers/`. Un dossier ARK importé ailleurs est seulement
  retiré du panel.

---

## 🧰 Python Toolbox

L'onglet **Python Toolbox** (`/toolbox.html`) liste automatiquement les fichiers `.py`
présents dans `scripts/` et permet de les exécuter sans passer par un terminal.

- **Liste des scripts :** `GET /api/scripts` renvoie le nom, la taille, la date de
  modification et la description extraite de la docstring (sans exécuter le script).
- **Sélection des dossiers :** `GET /api/fs/directories` renvoie les mondes Minecraft,
  les serveurs enregistrés et les dossiers du panel. L'interface les propose dans deux
  menus déroulants : le premier remplit le premier argument du script sélectionné
  (limité aux mondes pour `backup_world.py`), le second alimente le chemin de
  l'inventaire. La saisie manuelle d'un chemin reste toujours possible.
- **Exécution :** `POST /api/scripts/run` avec `{"script_name": "...", "args": ["..."]}`.
  Les arguments sont saisis un par ligne dans l'interface et transmis dans l'ordre.
  Le script s'exécute depuis la racine du panel ; `stdout`, `stderr` et le code de
  sortie sont affichés dans la console.
- **Limites :** 32 arguments maximum, 1 024 caractères par argument, 8 192 au total,
  délai de 5 minutes (variable `GOAT_SCRIPT_TIMEOUT`) et 1 Mo de sortie conservé par
  flux (variable `GOAT_SCRIPT_MAX_OUTPUT`). Le processus est tué si le délai expire.
- **Inventaire :** `GET /api/fs/export-listing?target_dir=<chemin>` génère un fichier
  texte (50 000 entrées maximum) téléchargeable depuis l'onglet.

Scripts fournis :

```bash
# Sauvegarde ZIP de <panel>/backups/backup_<serveur>_<date>.zip
python scripts/backup_world.py [id_serveur|dossier_serveur|dossier_world]

# Liste le contenu d'un dossier (défaut : racine du panel)
python scripts/list_contents.py [chemin]
```

Sans argument, `backup_world.py` choisit le premier serveur de `servers.json` ayant un
dossier `world/`. Avec un argument, la cible doit exister : le script s'arrête en erreur
(code 1) au lieu de sauvegarder un autre serveur. Un chemin pointant directement sur le
dossier `world/` d'un serveur connu conserve le nom de ce serveur dans l'archive.

---

## 🧱 Structure

| Fichier | Rôle |
| --- | --- |
| `main.py` | API FastAPI, WebSockets, auth, gestion des instances |
| `security.py` | Hachage du mot de passe et sessions signées |
| `mc_installer.py` | Téléchargement Vanilla / Forge / NeoForge / Sponge |
| `config.py` | Hôte, port, chemins |
| `public/` | Interface (HTML + Tailwind) |
| `servers/` | Instances gérées par le panel |
| `scripts/` | Scripts de la Python Toolbox |

---

## 📄 Licence

MIT — projet amateur, fourni sans garantie.
