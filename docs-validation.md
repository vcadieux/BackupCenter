# Development and tests / Développement et tests

## Python

Build and run tests without mounting production data:

```sh
docker build -t backupcenter:test .
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m \
  -v "$PWD/tests:/tests:ro" -e PYTHONPATH=/app \
  backupcenter:test python -m unittest discover -s /tests -v
```

Tests use temporary data and fixture credentials. They cover authentication, CSRF, rate limits, secret redaction, generated SSH keys and cleanup, configuration versions, site discovery/import, archive encryption, isolated extraction, pagination, English API messages, schedule migration, time validation, calendar recurrence DST transitions, authenticated APT plans, stale confirmations, backup exclusion and uncertain SSH outcomes. Maintenance commands are mocked; no real package upgrade is run by tests. They do not prove production recoverability.

## Frontend

```sh
docker run --rm -v "$PWD:/source:ro" \
  -e NODE_PATH=/tmp/qa/node_modules node:22-alpine sh -c \
  'npm install --prefix /tmp/qa --no-audit --no-fund jsdom@26.1.0 >/dev/null && BC_LANGUAGE=fr node /source/tests/test_ui.cjs && BC_LANGUAGE=en node /source/tests/test_ui.cjs'
```

These DOM tests preserve user identifiers and cover both languages, archive recovery selection, key prefill, public installation commands, and unsaved key reuse. They do not replace Safari/mobile visual verification.

## Translations

French source strings are message identifiers. `messages.json` maps them to English. Use `t()` for UI strings and `html`/`m()` for static HTML fragments; dynamic user values must remain separate. Run `python scripts/build-messages.py` after updating the catalog. Server responses localize application messages according to `X-BackupCenter-Language`; filenames, paths, domains, SQL names, and shell commands are not translated.

## Français

Les commandes ci-dessus exécutent les tests sur des données temporaires, sans monter les données de production. Les essais DOM couvrent les deux langues et préservent les identifiants utilisateur. Ils ne remplacent ni la vérification visuelle Safari/iPhone, ni un test de restauration réel.

Les messages source français servent d’identifiants; leurs traductions anglaises sont dans `messages.json`. Régénérez `public/messages.js` avec `python scripts/build-messages.py`. Gardez les noms de sites, bases, chemins et commandes hors des fragments traduits.

Conservez les résultats de production, captures, adresses réelles et notes d’exploitation dans `.private/`, jamais dans le dépôt public. Aucun test ne doit afficher de clé privée, cookie, secret TOTP ou mot de passe.
