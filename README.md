# 🐐 Genius Open Admin Tool (GOAT)

![Version](https://img.shields.io/badge/version-Pre--Alpha-amber)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-emerald)

> **« Projet amateur visant à faciliter l'administration de serveurs Minecraft et à la rendre accessible. »**

GOAT est un outil local d'administration et de supervision système conçu pour gérer des instances Minecraft (Vanilla, Forge, NeoForge, Sponge) sans abonnement, sans création de compte et sans dépendance externe.

---

## 🚀 Fonctionnalités Clés

- **Déploiement en 1-Clic :** Téléchargement automatisé des versions et chargeurs avec validation automatique de l'EULA.
- **Importation Simple :** Prise en charge des serveurs moddés existants via chemin absolu.
- **Console Temps Réel :** Streaming des logs JVM via WebSocket et terminal de commandes RCON interactif.
- **Éditeur de Configuration :** Modification directe de `server.properties` avec recherche intégrée.
- **Supervision Système :** Métriques CPU/RAM de l'hôte vs processus Java en direct.
- **Python Toolbox :** Outils d'automatisation et sauvegardes horodatées du dossier `world`.

---

## 🛠️ Installation & Lancement (Développement)

```bash
# Cloner le dépôt
git clone [https://github.com/TON_PSEUDO/GOAT.git](https://github.com/TON_PSEUDO/GOAT.git)
cd GOAT

# Créer et activer l'environnement virtuel
python -m venv .venv
.venv\Scripts\activate

# Installer les dépendances
pip install -r requirements.txt

# Lancer l'application en mode dev
python main.py