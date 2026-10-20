# Security / Sécurité

BackupCenter can hold privileged SSH and MySQL credentials. Serve it over HTTPS, enable TOTP, restrict access to trusted users, and keep archive recovery material outside the host.

Do not disclose secrets, database dumps, screenshots of credentials, setup codes, or private infrastructure details in public issues. If reporting a suspected vulnerability, describe a minimal reproduction using synthetic data and arrange a private reporting channel with the repository owner before sharing sensitive details.

Generated private SSH keys are sent only to the authenticated form, never embedded in the public installation command or signed session cookie. Stored credentials are encrypted with a separate local key. Host compromise can expose both: encrypted local storage is not a substitute for access control or an independent copy.

BackupCenter peut conserver des accès SSH et MySQL privilégiés. Utilisez HTTPS, activez le TOTP, limitez les accès et conservez les clés de récupération hors du serveur.

Ne publiez aucun secret, export SQL, code d’installation ou détail d’infrastructure privé dans les issues. Pour une faille présumée, utilisez des données fictives et convenez d’un canal privé avec le propriétaire du dépôt avant de partager des informations sensibles.
