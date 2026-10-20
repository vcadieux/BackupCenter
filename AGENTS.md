# BackupCenter contributor instructions

- Read README.md (or README.fr.md), docs-validation.md, and the relevant code before changes.
- Preserve existing archives, credentials, site isolation, and version checks.
- Never print, commit, publish, or include private keys, passwords, cookies, TOTP secrets, runtime databases, or setup codes in artifacts. Keep deployment-specific notes under ignored `.private/`.
- Preserve authentication, CSRF, encryption, SSH host verification, and configuration concurrency controls.
- One VPS connection may serve multiple sources. Keep manual configuration and legacy sources supported. Never assign every accessible database to every site without explicit selection.
- Generating a key for a new server may prefill the form. Never rotate an existing connection key automatically; keep public and private material separate.
- Update English/French messages and documentation together. Do not translate user-defined site names, SQL identifiers, paths, or shell commands.
- Restoration is isolated and must not overwrite production or import SQL into live databases without an explicit, separately reviewed workflow.
- Run relevant isolated tests; distinguish compilation, DOM testing, browser verification, and real restoration tests. A verified archive is not proof of a complete recovery.
- Communicate in the user's language. Do not create or publish GitHub repositories without the user's request.
