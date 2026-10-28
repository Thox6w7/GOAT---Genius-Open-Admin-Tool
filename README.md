# 🐐 Genius Open Admin Tool (GOAT)

![Version](https://img.shields.io/badge/version-Pre--Alpha-amber)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-emerald)

> **« Projet amateur visant à faciliter l'administration de serveurs de jeux et à la rendre accessible. »**

GOAT est un panel local sous Windows pour déployer, administrer et superviser des
serveurs de jeux. Pas d'abonnement, pas de compte à créer, pas de télémétrie :
une interface web, un mot de passe, et toutes vos instances au même endroit.

Le projet est **multi-jeux**. L'interface, la sécurité, le dashboard et la Python
Toolbox sont communs ; chaque jeu possède ensuite son propre onglet et son module
d'installation.

---

## 🎮 Jeux pris en charge

| Jeu | Versions / chargeurs | Déploiement | Suivi |
| --- | --- | --- | --- |
| **Minecraft Java** | Vanilla, Forge, NeoForge, Sponge | Téléchargement des versions officielles | CPU/RAM JVM, RCON, logs |
| **ARK: Survival Evolved** | Serveur SE (SteamCMD `376030`) | SteamCMD, 12 maps officielles | CPU/RAM, RCON, `ShooterGame.log` |

L'architecture sépare la logique par jeu (`mc_installer.py`, `ark_installer.py`) :
ajouter un nouveau jeu consiste à brancher un module et son onglet, sans toucher au
reste du panel.

---

## 🚀 Fonctionnalités communes

- **Un panel, plusieurs jeux :** Minecraft et ARK sont gérés depuis la même interface,
  avec la même page d'accueil, le même mot de passe et les mêmes mécanismes de
  démarrage, d'arrêt, de console et de suppression.
- **Déploiement automatisé :** téléchargement des versions et des chargeurs, validation
  de l'EULA pour Minecraft, installation ARK via SteamCMD.
- **Import de serveurs existants :** un dossier déjà installé sur le disque peut être
  repris sans réinstallation (chemin absolu).
- **Console temps réel :** logs diffusés par WebSocket et commandes RCON interactives
  pour les deux jeux.
- **Supervision :** métriques CPU/RAM de la machine et des processus de jeu, avec
  graphiques dynamiques.
- **Python Toolbox :** exécution de scripts maison, sauvegardes de mondes, inventaire
  de dossiers, le tout depuis le navigateur.
- **Accès protégé :** mot de passe unique, session signée, panel fermé par défaut au
  réseau local.
- **Guide intégré :** une page `/guide.html` accompagne la prise en main.

---

## 📦 Téléchargement

Les installeurs Windows sont publiés dans les
[releases GitHub](https://github.com/Thox6w7/GOAT---Genius-Open-Admin-Tool/releases).

Télécharge
[`GOAT_Setup_PreAlpha.exe`](https://github.com/Thox6w7/GOAT---Genius-Open-Admin-Tool/releases/download/v0.1.0-prealpha/GOAT_Setup_PreAlpha.exe),
puis lance-le. Les fichiers existants (`servers.json`, `servers/`, `backups/`) ne sont
pas écrasés par une mise à jour.

> L'installeur n'est pas signé : Windows SmartScreen peut afficher un avertissement au
> premier lancement. Le code source reste disponible pour compiler soi-même.

---

## 🛠️ Installation & lancement (développement)

```bash
# Cloner le dépôt
git clone https://github.com/Thox6w7/GOAT---Genius-Open-Admin-Tool.git
cd GOAT---Genius-Open-Admin-Tool

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

## 🟩 Minecraft (Java Edition)

L'onglet **Minecraft** (`/minecraft.html`) gère les serveurs Java, moddés ou non.

- **Création en 1 clic :** choix du chargeur (**Vanilla, Forge, NeoForge, Sponge**), de
  la version et de la RAM. Les listes de versions sont récupérées depuis les API
  officielles (`GET /api/mc/remote-versions`) ; `eula=true` est écrit automatiquement et
  `server.properties` est prérempli (ports, RCON, mot de passe).
- **Ports automatiques :** les ports de jeu et RCON sont incrémentés pour éviter les
  collisions entre plusieurs instances.
- **RAM :** la valeur `-Xmx` est appliquée au lanceur et peut être modifiée depuis
  l'interface (`POST /api/mc/servers/ram`).
- **Import :** un serveur existant est ajouté via son chemin absolu, avec détection de
  `run.bat` ou `server.jar` (`POST /api/mc/servers/import`).
- **Console :** logs JVM en direct par WebSocket et commandes RCON
  (`POST /api/mc/command`). La difficulté se change directement
  (`POST /api/mc/difficulty`).
- **Éditeur `server.properties` :** lecture et écriture avec recherche intégrée
  (`GET` / `POST /api/mc/properties`).
- **Arrêt :** arrêt propre par RCON, ou arrêt d'urgence (*panic*) qui tue le processus
  si le serveur ne répond plus.
- **Sauvegardes :** le script `backup_world.py` de la Toolbox crée une archive ZIP
  horodatée du dossier `world`.

---

## 🦖 ARK: Survival Evolved

L'onglet **ARK** (`/ark.html`) gère **ARK: Survival Evolved** (SE), via SteamCMD ou par
import d'une installation déjà présente sur le disque. Les cartes ne sont pas stockées
dans le dépôt : elles sont téléchargées avec l'app serveur Steam `376030`.

- **Création :** `POST /api/ark/servers/create` valide le nom, la version, la map et les
  ports (champs vides = attribution automatique), puis lance SteamCMD en arrière-plan
  (`+login anonymous +app_update 376030 validate`). L'avancement se suit avec
  `GET /api/ark/install/status?job_id=...` et s'écrit dans
  `logs/ark-server-<job>.log`.
- **Maps officielles :** The Island, Scorched Earth, The Center, Ragnarok, Aberration,
  Extinction, Valguero, Crystal Isles, Genesis: Part 1, Genesis: Part 2, Lost Island et
  Fjordur.
- **Versions :** deux choix à la création.
  - **Pré-Aquatica (358.24)** — sélectionnée par défaut et mise en avant : elle utilise
    la branche SteamCMD `-beta preaquatica` et reste compatible avec la majorité des
    mods existants. Recommandée pour jouer avec des mods ; les joueurs doivent aussi
    sélectionner la branche « preaquatica » dans Steam.
  - **Dernière version (Aquatica)** — branche publique. L'update Aquatica a changé le
    chargement des assets : de nombreux mods doivent être recuits ou ne fonctionnent
    plus. À réserver aux serveurs sans mods.
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
  `ShooterGameServer`). À défaut, l'exécutable est lancé avec les arguments URL
  (`?listen?SessionName=...?Port=...`) et `-server -log -NoBattlEye`.
- **Détection du processus :** par chemin de l'exécutable ARK dans le dossier du serveur
  (le cwd d'ARK est souvent `ShooterGame/Binaries/Win64`).
- **Logs :** `ShooterGame/Saved/Logs/ShooterGame.log` (repli sur
  `ShooterGameBackup.log`), diffusés dans la console du panel.
- **RCON :** active `RCONEnabled=True` et `RCONPort=<port>` dans le lanceur ARK, et
  utilise le même mot de passe que dans l'onglet ARK. L'arrêt propre envoie
  `SaveWorld` puis `DoExit` ; l'arrêt d'urgence fait de même puis tue le process si RCON
  est injoignable.
- **Découverte automatique :** un dossier ARK placé dans `servers/` est détecté au
  chargement (map `TheIsland`, ports 7777/27015/27020 par défaut).
- **Suppression :** comme pour Minecraft, les fichiers ne sont effacés que si le dossier
  se trouve dans `servers/`. Un dossier ARK importé ailleurs est seulement retiré du
  panel.

---

## 📊 Dashboard

Le **Dashboard** (`/dashboard.html`) affiche en temps réel l'utilisation CPU et la
mémoire de la machine hôte, avec des graphiques dynamiques alimentés par le WebSocket
`/ws/telemetry`. Les onglets de jeu ajoutent les métriques de leur processus (CPU/RAM
de la JVM Minecraft, CPU/RAM du serveur ARK) et l'état du serveur sélectionné.

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

## 🧱 Structure

| Fichier | Rôle |
| --- | --- |
| `main.py` | API FastAPI, WebSockets, authentification, gestion des instances |
| `security.py` | Hachage du mot de passe et sessions signées |
| `mc_installer.py` | Téléchargement et installation Minecraft (Vanilla/Forge/NeoForge/Sponge) |
| `ark_installer.py` | Catalogue ARK SE, versions, SteamCMD, jobs d'installation |
| `config.py` | Hôte, port et chemins |
| `public/` | Interface web (HTML + Tailwind) |
| `scripts/` | Scripts de la Python Toolbox |
| `servers/` | Instances gérées par le panel (ignoré par git) |
| `build_exe.py` / `installer.iss` | Build PyInstaller et installeur Inno Setup |

---

## 📄 Licence

MIT — projet amateur, fourni sans garantie.
