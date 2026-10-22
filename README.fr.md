# BackupCenter

[English](README.md) · Français

Interface de sauvegarde auto-hébergée pour les sites CloudPanel et les VPS Linux. Sauvegardez par SSH sur votre propre stockage, planifiez les copies, consultez les archives et testez la récupération dans des dossiers isolés.


Les sessions navigateur durent 30 jours et se renouvellent avec l’activité. La déconnexion efface la session du navigateur; un changement de mot de passe ou de TOTP révoque les autres sessions.

## Captures d’écran

Interface anglaise présentée avec des données fictives de démonstration; aucun serveur, accès ou fichier de sauvegarde réel n’est affiché.

**Tableau de bord : sources, horaires et état des sauvegardes**

![Tableau de bord : sources, horaires et état des sauvegardes](docs/images/backupcenter-overview.jpg)

**Archives : stockage, téléchargements et récupération isolée**

![Archives : stockage, téléchargements et récupération isolée](docs/images/backupcenter-archives.jpg)

## Fonctionnalités

- Interface français/anglais, choix mémorisé par navigateur, dates et messages d’API traduits.
- Génération d’une paire Ed25519 à l’ajout du serveur : clé privée préremplie et commande publique à copier dans une console fiable.
- Vérification des empreintes SSH. Les clés existantes ne sont jamais remplacées automatiquement.
- Une connexion par VPS, détection des dossiers CloudPanel et vhosts NGINX, regroupement des alias, choix explicite des sites et bases.
- Accès MySQL défini une fois par serveur, avec réglages propres à chaque site si nécessaire.
- Sauvegardes manuelles et planifiées, rétention et archives `.zip.age` chiffrées contenant fichiers, exports SQL et manifeste SHA-256.
- Vue Archives : filtre, pagination, emplacement sur le stockage, téléchargement ZIP ou chiffré.
- Authentification, TOTP facultatif, CSRF, identifiants chiffrés, journal et protection des modifications concurrentes.

## Installation Docker Compose

Docker Compose et un accès HTTPS via reverse proxy ou tunnel sont nécessaires. Le proxy fourni écoute par défaut sur `127.0.0.1:8080`.

```sh
cp .env.example .env
# Adapter le domaine, les chemins de stockage, l’écoute et le fuseau horaire.
docker compose up -d --build
```

Dirigez votre reverse proxy HTTPS vers cette adresse. Gardez `BACKUPCENTER_SECURE_COOKIE=1` en HTTPS. Un tunnel conteneurisé peut nécessiter une route réseau ou une adresse d’écoute différente. En production, privilégiez des chemins absolus : `/mnt/user/appdata/backupcenter/data` et `/mnt/user/backups/backupcenter` sur Unraid, par exemple.

Lisez le code d’installation uniquement dans votre terminal privé :

```sh
docker compose exec backupcenter cat /data/setup-code.txt
```

Saisissez-le dans l’interface, choisissez un mot de passe d’au moins 14 caractères, activez le TOTP et conservez la clé de récupération des archives hors du serveur. Sauvegardez aussi la configuration applicative de façon protégée.

## Ajouter un VPS

1. Saisir l’IP ou le domaine, le port SSH et l’utilisateur. Root est requis pour accéder à tous les sites et au système dans cette version; un compte limité voit seulement les ressources qu’il peut lire.
2. La clé privée dédiée est générée et préremplie. Copier seulement la **commande d’installation de la clé publique** dans le KVM ou une console déjà fiable. Les clés autorisées existantes sont conservées.
3. Enregistrer la connexion, comparer les empreintes SSH à celles de la console fournisseur, puis les confirmer.
4. Tester SSH, détecter les sites et confirmer les bases de chacun. Lancer une copie manuelle avant d’activer les horaires.

Avec root, la clé donne les privilèges administrateur. L’option `restrict` bloque les tunnels et le terminal interactif; elle ne limite pas les commandes root à la lecture. Une clé existante est conservée tant qu’on ne génère et n’enregistre pas de remplacement. Une nouvelle paire ne révoque pas les anciennes clés publiques sur le VPS.

Ne jamais coller une clé privée dans la console VPS, un chat, une issue ou un dépôt. Elle est transmise uniquement au formulaire authentifié en HTTPS, absente des cookies et chiffrée lorsque la connexion est enregistrée.

Voir le [guide SSH](docs/vps-access.md).

## MySQL et cohérence

Le VPS doit disposer de Python 3, GNU tar et des outils MySQL/MariaDB. Le mode automatique essaie `/root/.my.cnf` protégé ou la connexion MySQL locale par défaut. Root Linux ne garantit pas un accès MySQL : définir au besoin un fichier client distant protégé ou un compte MySQL. Aucun port MySQL public n’est nécessaire.

La détection lit les configurations `.env`, WordPress et PHP courantes sans exécuter le code. Une base accessible n’appartient pas forcément au site : vérifier les sélections. Les configurations dynamiques et arborescences personnalisées peuvent demander une saisie manuelle.

Les fichiers sont copiés en ligne et les exports utilisent des transactions. Cela ne garantit pas à lui seul la cohérence fichiers/SQL ni celle des tables non transactionnelles. Suspendre les écritures si l’application l’exige.

## Archives et restauration

La copie contient `files.tar.gz`, les exports SQL et `manifest.json`. Le ZIP est contrôlé, chiffré avec age puis déchiffré pour vérification avant validation atomique. La rétention s’exécute seulement après une nouvelle sauvegarde réussie.

Les téléchargements ZIP sont déchiffrés. Pour récupérer une archive chiffrée indépendamment de l’application :

```sh
age -d -i BackupCenter-recovery.key -o backup.zip archive.zip.age
```

La restauration actuelle extrait uniquement dans un **nouveau dossier isolé du serveur source**, avec Python 3.12+. Elle ne remplace pas les fichiers actifs et n’importe pas les bases de production. La reconstruction CloudPanel et la restauration système demandent une procédure distincte et testée.

La sauvegarde globale inclut les fichiers accessibles de la partition racine et toutes les bases logiques. Elle exclut les systèmes virtuels, `/mnt`, `/media`, les fichiers MySQL bruts, Docker et le swap. Les volumes montés séparément demandent des sources supplémentaires. Ce n’est **ni une image disque démarrable ni un snapshot OVHcloud**.

Les copies sont complètes, sans incrémental. Prévoir assez d’espace temporaire pour les fichiers compressés, le ZIP et sa copie de vérification. Les alertes externes et snapshots fournisseur automatiques ne sont pas implémentés.

## Développement et dépôt

Consulter les [tests](docs-validation.md). Les données, clés, `.env`, captures et notes personnelles doivent rester dans les dossiers privés ignorés. Ne pas les ajouter avec `git add -f`. `.env.example` contient uniquement des exemples génériques. Aucun dépôt GitHub n’est créé ou publié automatiquement.

Cette application peut conserver des accès SSH administrateur et des identifiants MySQL. Vérifier le code et les permissions avant un usage critique. Une archive vérifiée ne remplace pas un exercice de restauration.

## Heure des sauvegardes

L’heure par défaut est **02:00**, dans le fuseau `TZ` configuré. Elle se règle par source ou lors de la découverte des sites. Les fréquences quotidienne, hebdomadaire et de 30 jours conservent l’heure locale malgré les changements d’heure. Une heure inexistante au printemps est décalée du saut horaire; une heure répétée en automne ne s’exécute qu’une fois. Les sources existantes passent à 02:00. Les fréquences de moins de 24 heures se répètent à partir de l’heure choisie.

## Soutenir le projet

Si BackupCenter vous est utile, vous pouvez soutenir son développement via PayPal.

[☕ Buy me a coffee](https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=vcadieux%40vincentcadieux.com&item_name=BackupCenter&currency_code=CAD)

## Mises à jour du serveur

Dans la fiche du serveur, choisir **Mises à jour**. La vérification utilise la session connectée sans ressaisir les accès. L’installation exige le mot de passe et le TOTP si activé. La vérification exécute `apt-get update` puis simule `apt-get upgrade`; consulter les paquets, puis écrire **Oui** (ou **Yes**) pour installer dans les 15 minutes. Accès SSH root avec empreinte confirmée, Debian/Ubuntu et systemd requis. Les services peuvent redémarrer et les sites être interrompus. Les configurations locales sont conservées; aucun redémarrage automatique du VPS, suppression de paquet, autoremove ou changement de distribution. Les paquets retenus restent en attente. La maintenance et les sauvegardes BackupCenter sont sérialisées pour tous les serveurs; les tâches APT et sauvegardes externes échappent à ce verrouillage. Les opérations distantes continuent via systemd si SSH coupe; un résultat inconnu suspend les sauvegardes jusqu’à récupération du statut. Seuls les phases et noms de paquets sont affichés; aucun journal brut ni terminal interactif. Conserver une sauvegarde récente. Les métadonnées distantes restent dans `/var/lib/backupcenter-maintenance` pour diagnostic. Ceci gère les paquets système, pas les versions de CloudPanel.

## Terminal SSH interactif

Ouvrir **Terminal SSH** dans la fiche du serveur, saisir l’utilisateur du VPS à `login:`, puis son mot de passe à l’invite SSH. La session BackupCenter connectée est requise. Le terminal utilise uniquement le mot de passe; la clé de sauvegarde reste inchangée et n’est pas présentée pour cette connexion. Le VPS doit déjà autoriser les mots de passe SSH pour ce compte et l’allocation PTY. Pour root, la connexion root par mot de passe doit déjà être autorisée. BackupCenter ne modifie pas sshd, n’installe pas de clé et n’active pas les mots de passe root. L’empreinte SSH doit être confirmée.

Un seul terminal à la fois pour cette instance. Les sauvegardes et la maintenance BackupCenter sont suspendues jusqu’à sa fermeture, ainsi que les modifications des serveurs. Fermeture après 5 minutes sans saisie ou 30 minutes au total; le suivi automatique ne prolonge pas ces délais. Fermer la fenêtre, se déconnecter ou révoquer l’authentification ferme le processus SSH. Les tâches distantes détachées peuvent continuer; les commandes au premier plan peuvent être interrompues. Les travaux externes ne sont pas coordonnés.

Les saisies, mots de passe et transcriptions ne sont pas écrits dans la base ni dans le journal BackupCenter. La sortie reste en mémoire avec une limite de 1 Mio, puis est effacée peu après la fermeture; l’historique du shell et les journaux SSH dépendent du VPS. Le navigateur limite son historique et l’efface à la fermeture. Les séquences de presse-papiers distant, les liens cliquables et les redirections SSH sont désactivés. Le transport HTTPS fonctionne avec le proxy actuel, sans port supplémentaire ni configuration WebSocket. xterm.js 6.0.0 et fit addon 0.11.0 sont inclus avec leurs licences MIT dans `public/vendor/xterm`.
