# 3. The Vault password file

[Ansible can reach the host](02-first-ansible-run.md). Before deploying an app,
understand its two input files:

```text
~/.config/home-cluster/template.yml          application settings
~/.config/home-cluster/template-secrets.yml  encrypted secret values
```

`template` is the example app used in this guide. Existing apps have their own
names and files. Use the real app's existing files rather than creating empty
replacements.

## Three different passwords

The Vault password decrypts the encrypted YAML. App database/broker passwords
are values inside that YAML. Your sudo password is for privilege escalation.

The password file contains the Vault password only, on one line. It is plaintext.
Someone with that file and your encrypted Vault can read the secrets.

The established location is:

```text
~/.config/home-cluster/vault-password
```

An example prompt is:

> use the vault password file

If it already exists, the AI should use the path without displaying its
contents. It should not ask you to paste the password into chat.

## Create it only when needed

For a new local setup, create the protected directory and use an editor that
does not create swap/history files:

```sh
mkdir -p -m 700 "$HOME/.config/home-cluster"
(umask 077; vim -u NONE -n -i NONE "$HOME/.config/home-cluster/vault-password")
chmod 600 "$HOME/.config/home-cluster/vault-password"
```

Enter only the intended Vault password, then save. Do not replace an existing
password file casually: it must match existing Vault encryption. Changing this
file does not re-encrypt a Vault with a new password.

Keep it outside Git, backups or sharing arrangements that are not authorized
for secrets, and any prompt/log output. Protect access to the workstation too.

## Use the file in the command

```sh
ansible-playbook -i provision-hosts/inventory.yml deploy-app/deploy-code.yml \
  --limit sugar \
  -e @"$HOME/.config/home-cluster/template.yml" \
  -e @"$HOME/.config/home-cluster/template-secrets.yml" \
  --vault-password-file "$HOME/.config/home-cluster/vault-password"
```

This is the deployment shape, not an instruction to deploy before configuration
is ready. `-e @file` loads variables from a file. The encrypted file is decrypted
by Ansible using the password file.

Unlike `--ask-vault-pass`, this avoids an interactive Vault prompt. It does not
provide SSH authorization or sudo credentials.

Next: [Use the secrets script](04-secrets-script.md).
