# SSH access / Accès SSH

[English](../README.md) · [Français](../README.fr.md)

## English

Add a server in BackupCenter. A dedicated Ed25519 key is generated and the private field is filled automatically. Copy the **installation command**, not the private key, into your provider’s KVM console or another previously verified terminal. The command uses `sudo bash`, looks up the selected account’s home folder, sets SSH directory permissions, and appends a restricted public key only if it is not already present. Existing authorized keys are preserved.

The account must already exist. With `root`, the key permits administrator commands. `restrict` disables forwarding and interactive terminals but is not a read-only permission boundary. Public key authentication must be allowed by the server’s SSH policy; do not enable root passwords to work around an error.

To obtain host fingerprints through your trusted console:

```sh
for key in /etc/ssh/ssh_host_*_key.pub; do
    ssh-keygen -lf "$key" -E sha256
done
```

Compare each SHA256 fingerprint and key type with BackupCenter’s scan. Only then confirm and test SSH. The command that installs a key does not itself verify server identity.

You can paste your own complete, unencrypted dedicated private key instead. When editing an existing server, leaving the private field blank retains the stored key. Explicit generation of a replacement does not revoke old public keys from the VPS. Keys generated for an unsaved form are kept only in that page’s memory; refreshing the page discards them. Save the connection after installing its public key.

MySQL access is separate. Automatic mode uses a protected `/root/.my.cnf`, if present, or the local default client connection. Otherwise configure a protected remote connection file or database credentials once for the VPS. Never post credentials in issues or discussions.

## Français

Ajoutez un serveur dans BackupCenter. Une paire Ed25519 dédiée est générée et le champ privé est prérempli. Copiez la **commande d’installation**, jamais la clé privée, dans le KVM du fournisseur ou un terminal déjà vérifié. Elle utilise `sudo bash`, trouve le dossier de l’utilisateur choisi, applique les permissions SSH et ajoute la clé publique restreinte si elle n’est pas déjà présente. Les autres clés autorisées sont conservées.

Le compte doit exister. Avec `root`, cette clé permet des commandes administrateur. `restrict` bloque les tunnels et le terminal interactif, mais ne limite pas les commandes à la lecture. Le serveur doit autoriser l’authentification par clé; n’activez pas les mots de passe root pour contourner un refus.

Exécutez la boucle ci-dessus dans la console fiable pour lire les empreintes publiques. Comparez chaque valeur SHA256 et type de clé à ceux de BackupCenter avant de confirmer puis tester SSH. L’ajout d’une clé ne vérifie pas l’identité du serveur.

Vous pouvez coller votre propre clé privée dédiée complète, sans phrase de passe. Lors d’une modification, le champ vide conserve la clé enregistrée. Une nouvelle paire ne révoque pas les anciennes clés publiques du VPS. La paire d’un formulaire non enregistré reste uniquement en mémoire dans cette page : une actualisation la perd. Enregistrez la connexion après avoir installé sa clé publique.

L’accès MySQL est distinct. Le mode automatique utilise `/root/.my.cnf` protégé s’il existe, ou la connexion locale du client. Sinon, configurez une fois pour le VPS un fichier de connexion protégé ou des identifiants MySQL. Ne publiez jamais ces secrets dans une issue ou une discussion.
