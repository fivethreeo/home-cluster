# 4. Use the secrets script

With [the Vault password file](03-vault-password-file.md) in place, use the
existing script rather than decrypting secrets into a working file:

```text
deploy-app/update-secret.py
```

Suggested prompt:

> generate the template secrets with the script

The AI should find the app Vault, identify the existing keys, and use the
password-file option. It must not print the generated values.

## The keys must already exist

For a new example app only, create an encrypted file containing empty keys:

```sh
ansible-vault encrypt \
  --vault-password-file "$HOME/.config/home-cluster/vault-password" \
  --output "$HOME/.config/home-cluster/template-secrets.yml" <<'YAML'
django_secret_key: ""
app_database_password: ""
app_rabbitmq_password: ""
YAML
```

Do not run this against an existing secrets file. It is initialization, not
rotation. Do not deploy the empty values.

Generate each value:

```sh
./deploy-app/update-secret.py template django_secret_key \
  --vault-password-file "$HOME/.config/home-cluster/vault-password"
./deploy-app/update-secret.py template app_database_password \
  --vault-password-file "$HOME/.config/home-cluster/vault-password"
./deploy-app/update-secret.py template app_rabbitmq_password \
  --vault-password-file "$HOME/.config/home-cluster/vault-password"
```

Without supplied stdin, the script generates a random value. It requires Python
with PyYAML and the `ansible-vault` executable.

## Supplied values are different

Random generation fits passwords and application secret keys. It does not
create a valid provider-issued Domeneshop token.

For a replacement provider value, the key must already exist in the Vault.
The script accepts one line through stdin. In Bash, one way to enter a value
without putting it in shell history is:

```sh
IFS= read -r -s -p "Replacement token: " replacement
printf '\n'
printf '%s' "$replacement" | ./deploy-app/update-secret.py template domeneshop_token \
  --vault-password-file "$HOME/.config/home-cluster/vault-password"
unset replacement
```

Do not run this with shell tracing enabled or log the input. Do not generate
a random replacement for an actual API token. `--vault-dir` selects another
protected directory if required.

## What changes and what does not

The script replaces one existing top-level key, preserves other values, and
re-encrypts before replacing the file. It does not preserve YAML comments or
formatting. Unknown keys or failed updates leave the original Vault untouched.
Plaintext is not written to a temporary file.

It does not deploy, rotate the live service credential, or restart processes.
For existing apps, coordinate applying the changed service and runtime settings;
otherwise the old application password can stop matching the new database
password. Changing `django_secret_key` is also not an ordinary code update.

Next: [Deploy one minimal app](05-minimal-deployment.md).
