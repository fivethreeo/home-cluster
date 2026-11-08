#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

import yaml


def update_secret(vault, key, value, vault_options):
    if vault.is_symlink():
        raise ValueError("Refusing to replace a symlink")
    original = vault.read_bytes()
    decrypted = subprocess.run(
        ["ansible-vault", "decrypt", str(vault), "--output", "-", *vault_options],
        check=True,
        stdout=subprocess.PIPE,
    )
    data = yaml.safe_load(decrypted.stdout)
    if not isinstance(data, dict) or key not in data:
        raise ValueError("The key must already exist in a YAML mapping")
    data[key] = value

    descriptor, filename = tempfile.mkstemp(dir=vault.parent, prefix=f".{vault.name}.")
    os.close(descriptor)
    temporary = Path(filename)
    try:
        subprocess.run(
            ["ansible-vault", "encrypt", "--output", str(temporary), *vault_options],
            input=yaml.safe_dump(data).encode(),
            check=True,
            stdout=subprocess.PIPE,
        )
        if vault.read_bytes() != original:
            raise ValueError("The Vault file changed during the update")
        os.replace(temporary, vault)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(
        description="Update one app Vault key using stdin, or generate a random secret."
    )
    parser.add_argument("app", help="Application name matching <app>-secrets.yml")
    parser.add_argument("key", help="Existing top-level secret key to update")
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=Path.home() / ".config/home-cluster",
    )
    parser.add_argument("--vault-password-file", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", args.app):
        parser.error("Use an application name containing lowercase letters, digits or underscores.")

    try:
        piped = "" if sys.stdin.isatty() else sys.stdin.read()
        value = piped.removesuffix("\n").removesuffix("\r") if piped else secrets.token_urlsafe(32)
        if not value or "\n" in value or "\r" in value:
            raise ValueError("Provide a nonempty, single-line secret")
        options = (
            ["--vault-password-file", str(args.vault_password_file.expanduser())]
            if args.vault_password_file
            else ["--ask-vault-pass"]
        )
        vault = args.vault_dir.expanduser() / f"{args.app}-secrets.yml"
        update_secret(vault, args.key, value, options)
    except (OSError, ValueError, yaml.YAMLError, subprocess.CalledProcessError):
        print(
            "Vault update failed; check the file, key, credentials and input. "
            "The script did not replace the Vault file.",
            file=sys.stderr,
        )
        return 1
    print(f"Updated {args.key} in {vault}. Redeploy to apply it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
