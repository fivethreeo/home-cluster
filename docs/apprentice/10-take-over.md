# 10. Keep the setup usable

After [installation and connection](09-install-and-connect.md), or directly
after an existing-host update, leave enough context that the next person can
continue with short prompts.

## What the apprentice should be able to do

They can name the host, check Ansible connectivity, and distinguish an app
deployment from shared-service provisioning or a Debian reinstall.

They know the app configuration and encrypted Vault live outside Git.
They can use the Vault password file without printing it, and use
`update-secret.py` for an existing key without assuming it changes live services.

They can recognize when an app needs local frontend/backend builds before
`deploy-code.yml`. They know a playbook recap needs a page/service check too.

If they handle installation media, they can identify the USB before `dd`,
explain that the whole device is overwritten, and explain that the installer
has a second, separate disk-erasure step.

## Give the next conversation enough context

Suggested opening:

> working in home cluster, template on sugar
>
> deploy the update using the vault password file

That is short but establishes the app and target. The AI should inspect the
matching configuration and source before running anything.

If a file or access path is missing, that is a blocker, not a reason to recreate
an existing Vault with new random values.

## Transfer the access, not secret values in prose

The receiving maintainer needs authorized access to the application checkout,
this Ansible checkout, SSH identity, app config, encrypted secrets, and the
Vault password file through the existing protected process.

For backups they also need the actual restore procedure and authorized access.
An encrypted Vault without its password is not enough. The password file
without controlled access is not a safe handoff either.

Keep a small current-state note in an approved location:

```text
Host and inventory:
App and source checkout:
Config/Vault/password-file locations, not contents:
Latest deployment and checks:
Enabled services and public URL:
Backup coverage and tested recovery:
If installing: ISO identity, machine target, installation state:
Remaining work or missing access:
```

Use actual values. Do not record USB device names as permanent facts; identify
the device again for each write.

## Finish by doing a small task together

Let the apprentice run a connection check and prepare an app update using the
existing password file. Have them explain the script's output and the deployment
result. If installation is part of their role, practise on disposable hardware
or a deliberately isolated VM with no conflicting IP.

Practise restoring backups separately in an isolated environment before claiming
recovery is covered. Do not overwrite live data as an onboarding exercise.

Handoff is complete when the apprentice can repeat the demonstrated procedure
with working authorized access and knows how to stop for an ambiguous disk,
failed check, or credential problem. They do not need to memorize every flag;
they need to understand which operation they are asking the AI to perform.
