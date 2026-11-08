# From ISO to deployed Prosango

This walkthrough deploys **Prosango on `sugar` (`192.168.1.100`)**, initially
using HTTP and its IP address. Run commands from the `ansible-home-cluster`
repository unless a step says otherwise.

**Sugar already has infrastructure installed.** The ISO/install steps are only
needed for a fresh installation. Re-provisioning an existing server now uses
the newer RabbitMQ repositories: review upgrade requirements and back up broker
data first.

## 1. Configure and build the ISO

Review `generate-isos/iso-builds.yml`:

- Host: `sugar`, IP: `192.168.1.100`.
- Gateway, DNS and network settings match your LAN.
- `iso_authorized_key_file` points to your SSH **public** key.
- `iso_username` matches the inventory's SSH user.

Then build:

```sh
cd generate-isos
ansible-galaxy collection install -r requirements.yml
ansible-playbook build-isos.yml --ask-become-pass
cd ..
```

The image will be:

```text
generate-isos/output/sugar-debian-preseed.iso
```

## 2. Install Debian

Write the ISO to USB with an ISO-writing tool, boot `sugar` from it, and let
installation finish.

**Warning: the unattended installer erases the first detected disk.** Back up
anything needed and disconnect disks you must preserve.

After installation, remove the installation media and boot from the installed
disk. Python, SSH and passwordless sudo for `sugar` are included.

## 3. Verify SSH and Ansible

```sh
ssh -i ~/.ssh/id_ecdsa_sugar sugar@192.168.1.100
```

Verify the host-key fingerprint before accepting it, then exit SSH.

If you reinstalled and get a changed-host-key warning, remove the old entry
**only after confirming the replacement is expected**:

```sh
ssh-keygen -R 192.168.1.100
```

Reconnect and verify the new fingerprint. Check
`provision-hosts/inventory.yml` has the correct address, username and private-key
path, then:

```sh
ansible -i provision-hosts/inventory.yml sugar -m ping
```

Expect `"ping": "pong"`.

## 4. Provision infrastructure

The inventory already enables PostgreSQL, RabbitMQ and Nginx on `sugar`.

```sh
ansible-playbook -i provision-hosts/inventory.yml \
  provision-hosts/provision.yml --limit sugar
```

This installs infrastructure, not the application.

## 5. Create app configuration and encrypted secrets

Keep both files outside the repository:

```sh
mkdir -p -m 700 "$HOME/.config/home-cluster"
```

Create **`~/.config/home-cluster/prosango.yml`** using a heredoc:

```sh
cat > "$HOME/.config/home-cluster/prosango.yml" <<'YAML'
app_name: prosango
app_domains:
  - 192.168.1.100

app_media_enabled: true
celery_enabled: true
celery_beat_enabled: true

ddns_enabled: false
certbot_enabled: false
YAML
```

Create the encrypted secrets file from a heredoc without opening an editor.
Use `ansible-vault encrypt` (not `create`) to read the empty keys from stdin:

```sh
ansible-vault encrypt \
  --output "$HOME/.config/home-cluster/prosango-secrets.yml" <<'YAML'
django_secret_key: ""
app_database_password: ""
app_rabbitmq_password: ""
YAML
```

It prompts for a Vault password. Use this only for a **new file**, not an
existing secrets file.

For prompt-free secret updates, create a password file containing only the
same Vault password used above, on one line:

```sh
(umask 077; vim -u NONE -n -i NONE "$HOME/.config/home-cluster/vault-password")
chmod 600 "$HOME/.config/home-cluster/vault-password"
```

**The password file is plaintext: keep it outside Git and protect it.**
It must match the password currently encrypting the Vault.

Generate the application secrets using that password file:

```sh
./deploy-app/update-secret.py prosango django_secret_key \
  --vault-password-file "$HOME/.config/home-cluster/vault-password"

./deploy-app/update-secret.py prosango app_database_password \
  --vault-password-file "$HOME/.config/home-cluster/vault-password"

./deploy-app/update-secret.py prosango app_rabbitmq_password \
  --vault-password-file "$HOME/.config/home-cluster/vault-password"
```

Both decryption and encryption use the password file without prompting.
**Do not deploy with the empty values.**

## 6. Build the frontend locally

Use the Prosango feature branch:

```sh
cd ../prosango-project
git switch feat/celery-env-postgresql
python3 run.py npm ci
python3 run.py npm run build
cd ../ansible-home-cluster
```

The deployment consumes `typescript-frontend/dist`. **It does not install Node
or run Rspack on the server.**

## 7. Deploy the application

Install the deployment collections:

```sh
ansible-galaxy collection install -r deploy-app/requirements.yml
```

Then deploy:

```sh
ansible-playbook -i provision-hosts/inventory.yml deploy-app/deploy-code.yml \
  --limit sugar \
  -e @"$HOME/.config/home-cluster/prosango.yml" \
  -e @"$HOME/.config/home-cluster/prosango-secrets.yml" \
  --ask-vault-pass
```

This creates the application user, database and RabbitMQ namespace, uploads code
and prebuilt assets, writes the protected runtime `.env`, installs Python
dependencies, runs migrations and `collectstatic`, and starts Gunicorn,
Celery/Beat and the Nginx site.

You **do not** need to run `deploy.yml` separately.

## 8. Create your Django administrator

```sh
ssh -t -i ~/.ssh/id_ecdsa_sugar sugar@192.168.1.100 \
  'cd /srv/prosango/current/django-project &&
   sudo -u prosango env DJANGO_ENV_FILE=/srv/prosango/.env \
   /srv/prosango/.venv/bin/python manage.py createsuperuser'
```

Open:

```text
http://192.168.1.100/
http://192.168.1.100/admin/
```

Static assets are served under `/static/`; uploaded media under `/media/`.
**Media is public**, so restrict uploaded file types.

For service status and logs, SSH into `sugar` and run:

```sh
sudo systemctl status gunicorn-prosango celery-prosango celerybeat-prosango nginx
sudo journalctl -u gunicorn-prosango -u celery-prosango -u celerybeat-prosango -n 100 --no-pager
```

Celery/Beat are ready to run jobs, but application tasks and schedules still need
to be defined in Prosango.

## Optional Memcached

To add the cache to an existing server without rerunning RabbitMQ or the other
infrastructure roles:

```sh
ansible-playbook -i provision-hosts/inventory.yml provision-hosts/provision.yml \
  --limit sugar --tags memcached -e memcached_enabled=true
```

For future provisioning runs, optionally keep `memcached_enabled: true` on
`sugar` in `provision-hosts/inventory.yml`. The service listens only on localhost,
with UDP disabled and a default 64 MB item-cache budget.

Add this setting to `~/.config/home-cluster/prosango.yml`:

```yaml
app_memcached_enabled: true
```

Then repeat the code-deployment command from step 7. It installs the Python
client, configures Django's cache, and checks real cache writes and reads.
No additional secrets are required.

Production and staging can share the same cache service: each uses its
`app_name` as a cache-key prefix. Prefixes avoid collisions, not unauthorized
access. This enables Django's cache API, not automatic page caching.

The app setting defaults to false. Disable it and redeploy to return to
local-memory caching; the shared Memcached service is retained.

## Optional TypeScript HTTP and WebSocket services

Prosango has separate `typescript-backend/` and `typescript-ws-backend/`
directories beside `typescript-frontend/` and `django-project/`.
Build the new services locally:

```sh
cd ../prosango-project
python3 run.py npm-backend ci
python3 run.py npm-backend run build
python3 run.py npm-ws-backend ci
python3 run.py npm-ws-backend run build
cd ../ansible-home-cluster
```

Add the desired component switches and locations to the app YAML:

```yaml
app_django_enabled: true
app_ts_backend_enabled: true
app_ts_ws_backend_enabled: true
app_django_location: /
app_ts_backend_location: /api/
app_ts_ws_backend_location: /ws/
app_ts_backend_port: 8001
app_ts_ws_backend_port: 8002
```

Repeat the deployment command from step 7. Test the HTTP backend at
`http://192.168.1.100/api/healthz` and connect a WebSocket client to
`ws://192.168.1.100/ws/`. With public TLS, use HTTPS and WSS instead.
These are health/JSON and echo starters; add authentication and application
features before handling private data.

Django remains enabled by default; the new components default to disabled.
Turning a component off stops/disables its service and removes its Nginx route.
A Node-only stack does not require Django code, frontend builds, or database
secrets; Celery requires Django. No TypeScript compilation occurs on servers.

For separate servers, set `app_django_host`, `app_ts_backend_host`, and
`app_ts_ws_backend_host` to inventory aliases, with explicit private bind
addresses. Limit deployment to the Nginx gateway, not the backend hosts.
See `deploy-app/README.md` for a complete split-server inventory and app YAML,
private Django static/media origin, firewall requirements, and host-move steps.

## Future releases

Build the frontend locally again, then repeat the deployment command from step 7.
The server uses your **local checkout**, not a Git pull.

**Plan downtime and back up PostgreSQL plus `/srv/prosango/media` before
schema-changing releases.** Deployment restarts managed application processes,
preserves uploads/database data, and has no automatic rollback.
