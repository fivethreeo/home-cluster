# 6. Make the next change

After [one app works](05-minimal-deployment.md), your next message can be short:

> deploy the update

In context, the AI should use the same app, target, configuration, and Vault
password file. It should not generate new passwords or re-provision shared
services simply because source changed.

## Build only the selected components

With the minimal Django-only configuration, there is no frontend build to
upload. If you later enable the frontend, build in the application checkout:

```sh
python3 run.py npm run build
```

For enabled TypeScript services, use their matching wrappers:

```sh
python3 run.py npm-backend run build
python3 run.py npm-ws-backend run build
```

The deployment consumes prebuilt output. It does not compile TypeScript or
build the frontend on the server. Install missing dependencies using the
application's committed lockfiles/setup, not arbitrary global packages.

The source must be the intended release. A clean-looking playbook run cannot
prove you packaged the correct checkout.

## Add infrastructure features when you actually need them

Public domains and HTTPS need appropriate DNS/certificate configuration.
Domeneshop credentials belong in the Vault. The secrets script can replace
issued credentials, but cannot invent valid ones.

Multiple apps on one host need distinct domains or compatible mount paths,
cookies, ports, and shared-site settings. The existing `deploy-app/README.md`
documents those choices; do not reuse the same root URL and sockets by accident.

If media is enabled, the `/media/` alias is public. Do not treat it as authorized
private file access.

Existing static storage may also contain published app downloads. Preserve
the target's deliberate collection settings rather than clearing it casually.

## Password changes are not code changes

Updating a Vault value changes the encrypted input, not the running service.
Apply changes to both service credentials and application runtime consistently.
Do not rotate secrets on every deployment.

The Vault password file stays the same unless Vault rekeying is intended.
Changing its contents alone will not rekey existing encrypted files.

## Backups are a separate responsibility

The minimal example deliberately leaves backups disabled. That is not a
production recommendation. Before putting valuable data on the app, arrange
backups and test recovery.

This repository has a backup playbook and a detailed backup/restore section
in `deploy-app/README.md`. Enabling its backup feature can provision cloud
resources by default. Do not enable it as a silent side effect of an update,
or overwrite existing backup configuration with the minimal example.

Next: [Build an installer ISO](07-build-an-iso.md), only if you need a fresh
machine. Existing-host app work can skip to [handoff](10-take-over.md).
