# MobiDesk Pro

Application desktop simple de **gestion du stock d'afficheurs** pour boutique
de téléphones : ajout, modification, suppression, ajustement de quantité
(entrée/sortie tracée) et alerte de stock bas.

Fonctionne **entièrement hors ligne** sur Windows, sans serveur ni hébergement.

## Technologies

- Python 3.12+
- PySide6 (interface graphique)
- SQLite + SQLAlchemy (persistance)
- pytest (tests automatisés)

## Installation (développement, Windows)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Lancement de l'application

```powershell
python main.py
```

La base de données SQLite est créée automatiquement au premier lancement.

## Connexion

L'application demande un mot de passe au démarrage (compte unique partagé,
pas de gestion multi-utilisateurs). Le mot de passe par défaut à la
première installation est `mobidesk2026` — à changer immédiatement depuis
**Paramètres → Sécurité → Changer le mot de passe**.

Le mot de passe n'est jamais stocké en clair (hash salé dans
`auth.json`, à côté de `stock.db`). **En cas d'oubli**, il n'existe pas de
récupération intégrée : supprimer le fichier `auth.json` dans le dossier
de données (visible depuis Paramètres → "Ouvrir le dossier") réinitialise
au mot de passe par défaut, sans toucher aux données de stock.

## Lancement des tests

```powershell
pytest
```

## Fonctionnalités

- Liste des afficheurs avec recherche (référence, marque, modèle, couleur)
- Filtre "stock faible uniquement"
- Ajout / modification d'un afficheur (référence, marque, modèle compatible,
  qualité, couleur, prix d'achat, prix de vente, stock minimum, notes)
- Ajustement de stock (entrée ou sortie) avec motif obligatoire — le stock ne
  peut jamais devenir négatif
- Historique complet des mouvements de stock
- Suppression logique (un afficheur supprimé disparaît de la liste mais son
  historique de mouvements est conservé)

## Emplacement des données

En mode développement, les données sont stockées dans `./data/stock.db`.

En mode installé (exécutable), les données sont stockées dans :

```
C:\Users\<utilisateur>\AppData\Local\MobiDeskPro\data\stock.db
```

Ce choix garantit que les données survivent aux mises à jour et
désinstallations de l'application, contrairement à un stockage dans le
dossier d'installation (qui peut être en lecture seule, sur une clé USB
protégée en écriture, ou supprimé lors d'une mise à jour).

## Générer un exécutable Windows (.exe)

Pour installer l'application sur un autre PC Windows (sans avoir Python
installé dessus), génère un exécutable autonome avec PyInstaller.

**Sur la machine de développement** (celle-ci) :

```powershell
build_windows.bat
```

Ce script installe les dépendances, exécute les tests, puis génère
`dist\MobiDeskPro.exe` — un unique fichier d'environ 55 Mo qui contient
Python, PySide6 et toute l'application.

Deux formes de distribution sont disponibles à chaque release :

- **`MobiDeskProSetup.exe`** (recommandé pour toute nouvelle installation) —
  un vrai installeur Windows (voir section suivante) : installation dans
  `Program Files`, raccourcis Bureau/Menu Démarrer, désinstallation propre
  via le Panneau de configuration.
- **`MobiDeskPro.exe`** — l'exécutable brut, portable, sans installation.
  Conservé pour compatibilité avec les postes déjà en place et pour un usage
  ponctuel (clé USB, dépannage).

**Sur le PC de destination (méthode portable) :**

1. Copie uniquement `MobiDeskPro.exe` (par clé USB, réseau partagé, etc.)
   — aucune installation de Python ni d'autre logiciel n'est nécessaire.
2. Double-clique dessus pour lancer l'application.
3. Au premier lancement, l'application crée automatiquement son dossier de
   données dans `C:\Users\<utilisateur>\AppData\Local\MobiDeskPro\data\`.

Chaque PC aura donc sa **propre base de données locale**, indépendante des
autres — il n'y a pas de synchronisation entre plusieurs postes. Pour
transférer les données d'un PC à un autre, copie simplement le fichier
`stock.db` de ce dossier vers le même emplacement sur le nouveau poste.

## Générer l'installeur Windows (Inno Setup)

Pour une installation classique (raccourcis, désinstallation propre), un
script [Inno Setup](https://jrsoftware.org/isinfo.php) génère
`MobiDeskProSetup.exe` à partir de l'exécutable déjà buildé.

**Prérequis** : installer [Inno Setup 6](https://jrsoftware.org/isdl.php)
sur la machine de développement (une seule fois).

```powershell
build_windows.bat
build_installer.bat
```

`build_installer.bat` lit automatiquement la version depuis
`app/version.py` et produit `dist\MobiDeskProSetup.exe`.

L'installeur demande les droits administrateur **une seule fois**, à
l'installation initiale — les mises à jour automatiques déclenchées
ensuite depuis l'application ne redemandent jamais cette élévation, car le
dossier d'installation est configuré pour rester modifiable par
l'utilisateur courant.

### Transition depuis une installation portable existante

Un poste qui utilisait déjà `MobiDeskPro.exe` en mode portable ne migre pas
automatiquement vers l'installeur (la mise à jour automatique remplace
l'exécutable à son emplacement actuel, elle ne le déplace jamais). Pour
passer à l'installeur sur un poste déjà en place :

1. Fermer l'application.
2. Télécharger et exécuter `MobiDeskProSetup.exe` (une seule invite
   d'élévation à accepter).
3. Supprimer manuellement l'ancien exécutable portable si souhaité.

Les données (`stock.db`) ne sont jamais affectées par cette transition,
quel que soit l'emplacement de l'exécutable.

## Mises à jour automatiques

L'exécutable installé peut vérifier et installer les nouvelles versions
directement depuis l'onglet **Paramètres** (bouton "Vérifier les mises à
jour"). C'est le **seul appel réseau** de toute l'application — le reste
(base SQLite locale) continue de fonctionner entièrement hors ligne, y
compris si aucune connexion internet n'est disponible.

Les mises à jour sont distribuées via les **GitHub Releases** du dépôt
[`wmih-perso/mobidesk-pro`](https://github.com/wmih-perso/mobidesk-pro)
(dépôt public, requis pour que l'app puisse consulter la dernière release
sans authentification).

### Publier une nouvelle version

1. Mettre à jour le numéro de version dans **deux fichiers** (à garder
   synchronisés manuellement) :
   - `app/version.py` → `APP_VERSION`
   - `pyproject.toml` → `version`
2. Lancer `build_windows.bat` pour générer `dist\MobiDeskPro.exe`.
3. Lancer `build_installer.bat` pour générer `dist\MobiDeskProSetup.exe`.
4. Publier une Release GitHub taguée `vX.Y.Z` avec **les deux fichiers**
   attachés comme assets :
   ```powershell
   gh release create vX.Y.Z dist\MobiDeskPro.exe dist\MobiDeskProSetup.exe --title "vX.Y.Z" --notes "..."
   ```
   ou via l'interface GitHub (Releases → Draft a new release).

   **Important** : l'asset portable doit s'appeler exactement
   `MobiDeskPro.exe`, sinon la mise à jour automatique ne le retrouvera
   pas. Les deux fichiers doivent être attachés à chaque release — l'exe
   portable reste nécessaire pour que les postes déjà installés en mode
   portable continuent de recevoir les mises à jour automatiques.

Chaque client installé peut alors cliquer sur "Vérifier les mises à jour"
pour télécharger et installer automatiquement la nouvelle version — le
remplacement de l'exécutable se fait via un petit script qui attend la
fermeture de l'application, remplace le fichier, puis relance
automatiquement la nouvelle version.

### Remarques

- Le premier lancement de l'exécutable peut prendre quelques secondes de
  plus que les suivants (décompression interne).
- Certains antivirus signalent parfois, à tort, les exécutables générés par
  PyInstaller comme suspects (faux positif fréquent avec cet outil, lié à la
  façon dont Python est empaqueté — pas à un vrai problème de sécurité).

  **Si SmartScreen bloque au double-clic** ("Windows a protégé votre
  ordinateur") :
  1. Cliquer sur *Informations complémentaires* (en bas à gauche de la
     fenêtre bleue).
  2. Cliquer sur *Exécuter quand même*.

  **Si Windows Defender met le fichier en quarantaine** :
  1. Ouvrir *Sécurité Windows* → *Protection contre les virus et menaces*
     → *Historique de protection*.
  2. Trouver `MobiDeskPro.exe` → *Autoriser sur l'appareil*.

  **Pour éviter que ça se reproduise à chaque nouvelle version**, ajouter
  une exclusion permanente : *Sécurité Windows* → *Protection contre les
  virus et menaces* → *Gérer les paramètres* → *Exclusions* → *Ajouter ou
  supprimer des exclusions* → *Ajouter une exclusion* → *Fichier*, et
  sélectionner l'emplacement où `MobiDeskPro.exe` sera installé.
