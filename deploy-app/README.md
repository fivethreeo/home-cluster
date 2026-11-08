# Application deployment

Provision infrastructure with `provision-hosts/` first, then configure one
application per invocation on its web host:

```sh
ansible-galaxy collection install -r deploy-app/requirements.yml
ansible-playbook -i provision-hosts/inventory.yml deploy-app/deploy.yml \
  --limit sugar -e @app.yml -e @secrets.yml --ask-vault-pass
```

Create `app.yml` outside the repository (or supply equivalent host variables):

```yaml
app_name: example
app_domains:
  - example.com
  - www.example.com
app_upstream: http://127.0.0.1:8000
ddns_enabled: true
certbot_enabled: true
certbot_email: admin@example.com
```

Use `ansible-vault create secrets.yml` outside the repository and define
`app_database_password`, `app_rabbitmq_password`, `domeneshop_token`, and
`domeneshop_secret`. Do not commit plaintext credentials.

To update a single existing key, name the encrypted file
`~/.config/home-cluster/<app_name>-secrets.yml` and run:

```sh
./deploy-app/update-secret.py example app_database_password
your-secret-generator | ./deploy-app/update-secret.py example app_rabbitmq_password
```

Without stdin the script generates a random secret; piped input supplies the
replacement value (one trailing newline is removed). It preserves other keys
but not YAML comments/formatting. Unknown keys and failed updates leave the
original file untouched; plaintext is never written to a temporary file.
Use `--vault-dir /path/to/vaults` for another directory and
`--vault-password-file /secure/path/vault-password` for unattended operation.
Without a password file, Vault prompts separately for decryption and encryption;
enter the same Vault password both times. The script requires Python with PyYAML.

Redeploy using the updated file to apply service credentials, then update
application runtime credentials and restart its processes. The script does not
deploy or restart anything. Random generation is suitable for passwords, not
Domeneshop API tokens; pipe a provider-issued replacement token instead.

Deployment creates a locked application Linux user with home `/srv/example`,
an owned PostgreSQL database and non-administrative database login, SCRAM
`pg_hba.conf` rules scoped to that database/user, and a RabbitMQ vhost/user
restricted to that vhost. Password changes are applied on subsequent runs.
Nginx stays `www-data`, serves `/srv/example/static/` at `/static/`, and proxies
other requests to `app_upstream`. Application code, its web/backend service, and
runtime environment are not installed by this playbook. Optional Celery service
units are managed once those prerequisites exist.

Database, database-user, RabbitMQ-vhost, RabbitMQ-user and Linux-user names
default to `app_name`; override `app_database`, `app_database_user`,
`app_rabbitmq_vhost`, `app_rabbitmq_user`, or `app_user` if needed. Use unique
names for each application; shared users are not supported. Resources are not
deleted when an application name changes.

## Separate service hosts

PostgreSQL and RabbitMQ default to the web host. To use separate hosts, include
them in the same inventory and set their inventory names in `app.yml`:

```yaml
app_postgresql_host: database
app_postgresql_client_addresses:
  - 192.168.1.100/32
app_postgresql_listen_addresses: 192.168.1.101
app_rabbitmq_host: broker
```

Tasks and PostgreSQL handlers delegate to those hosts even with `--limit` set
to the web host. Provision their services beforehand. Explicitly setting
`app_postgresql_listen_addresses` changes the server-wide listener and restarts
PostgreSQL; otherwise listeners are left unchanged. Configure firewall access
and RabbitMQ network listeners separately. Never use an unrestricted database
CIDR unless that access is intended.

## Static files

Deployment always creates `/srv/<app_name>/static/` and serves it at `/static/`.
The directory is owned by the application user with group `www-data` and mode
`2750`, so collected files inherit Nginx's group. Existing files are not
recursively changed and must be group-readable.

For Django, configure `STATIC_ROOT = "/srv/example/static"` and
`STATIC_URL = "/static/"`. Run `python manage.py collectstatic --noinput` as the
application user during a release; this playbook does not run it for you. The
upload permission settings below also apply to Django's default collected
static-file permissions.

## Public uploaded media

Add `app_media_enabled: true` to `app.yml` and rerun deployment to serve uploads
publicly at `/media/`. Deployment creates `/srv/<app_name>/media/`, owned by the
application user with group `www-data` and mode `2750`. The setgid bit makes
new files and subdirectories inherit that group; the application must also give
the group read/traverse permissions.

For a Django application named `example`, configure:

```python
STATIC_ROOT = "/srv/example/static"
STATIC_URL = "/static/"
MEDIA_ROOT = "/srv/example/media"
MEDIA_URL = "/media/"
FILE_UPLOAD_PERMISSIONS = 0o640
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o2750
```

Keep uploads outside release/code directories and back up this directory
separately. Deployment does not delete or recursively change existing uploads;
existing files must already be readable by `www-data`. Disabling the setting
removes the Nginx media alias but retains uploaded files.

These uploads require no login. Directory listing and symlink serving are
disabled, and responses have `X-Content-Type-Options: nosniff`, but this does not
make arbitrary HTML/SVG uploads safe on the application origin. Restrict upload
types or use a separate cookieless media domain for untrusted active content.
Private media needs application authorization instead of this public alias.

## DNS and certificates

Both features are opt-in and use Domeneshop credentials stored root-only on the
web host. DDNS updates `ddns_domains` (default: `app_domains`) immediately and
hourly via a per-application systemd timer. Domeneshop detects the requesting
host's public address; set `ddns_ip` to explicitly supply an IPv4/IPv6 address.

Certbot and `certbot-dns-domeneshop` are installed in an isolated virtual
environment. DNS-01 certificates cover `certbot_domains` (default:
`app_domains`); all Nginx names must be covered. The default DNS propagation
wait is 120 seconds, configurable with `certbot_propagation_seconds`.
Certificates are obtained before Nginx switches to HTTPS, and existing
certificates are renewed when due or when domain names change. A per-application
timer checks renewal twice daily and validates Nginx before reloading it.

Setting either feature to false disables its previously managed timer.
Disabling Certbot returns the site to HTTP but retains certificates and
credentials. Existing timers/certificates from the shell script or the old
provisioning workflow are not migrated or removed automatically.

## Celery worker and Beat

Enable either service independently in `app.yml`, after installing the application,
its dependencies (including Celery), and a protected runtime environment file:

```yaml
celery_enabled: true
celery_beat_enabled: true
celery_app: project
app_code_dir: /srv/example/current
app_virtualenv: /srv/example/.venv
app_environment_file: /srv/example/.env
```

`celery_app` is the Python module/app passed to Celery's `--app` option, not
necessarily `app_name`. The runtime file must use systemd `EnvironmentFile`
syntax (`KEY=value`, not shell commands) and supply settings such as Django's
secret key and database connection. Application paths must be accessible to
the application user.

Deployment manages `celery-example.service` and `celerybeat-example.service`,
running as the application user. A separate root-only environment file supplies
`CELERY_BROKER_URL` using the application's RabbitMQ user/vhost and the selected
broker host; override `celery_broker_host` if its application-facing address
differs from the inventory connection address. Broker password changes restart
enabled Celery services automatically.

Both services start on boot, restart on failure, and log to the systemd journal.
Workers receive a graceful termination signal and have up to 300 seconds to
finish active tasks before forced shutdown. Beat stores its persistent schedule
under `/var/lib/celery/example/`, outside the application code directory.

Enable Beat on only one host per application schedule. Multiple workers are
fine; multiple Beat instances can enqueue duplicate tasks. Unit/config changes
restart services, but replacing application code alone does not: restart them
as part of the release workflow.

```sh
sudo systemctl status celery-example celerybeat-example
sudo journalctl -u celery-example -u celerybeat-example
```

Setting a flag to false stops/disables its previously managed service, retaining
the unit, credentials, and Beat schedule. Existing units from the original shell
installer are not automatically migrated or stopped.

## Validation

Run focused tests with a Python environment containing Ansible's Jinja2 dependency:

```sh
python -m unittest discover -s deploy-app/tests -v
ansible-playbook -i provision-hosts/inventory.yml deploy-app/deploy.yml --syntax-check
```

## Deploy Prosango code and prebuilt assets

Use `deploy-code.yml` to deploy the sibling Prosango checkout. Build the frontend
yourself beforehand, using the same checkout/branch as the Django code:

```sh
cd ../prosango-project
python run.py npm ci
python run.py npm run build
cd ../ansible-home-cluster
```

Example `app.yml` for IP-only hosting:

```yaml
app_name: prosango
app_domains: ["192.168.1.100"]
app_source_dir: ../prosango-project
app_frontend_dist: ../prosango-project/typescript-frontend/dist
app_media_enabled: true
celery_enabled: true
celery_beat_enabled: true
ddns_enabled: false
certbot_enabled: false
```

Paths supplied in `app_source_dir` and `app_frontend_dist` are controller paths;
prefer absolute paths when overriding them. Defaults locate the sibling checkout
relative to the playbook. Supply `django_secret_key`, `app_database_password`,
and `app_rabbitmq_password` in your Vault file:

```sh
ansible-playbook -i provision-hosts/inventory.yml deploy-app/deploy-code.yml \
  --limit sugar -e @app.yml \
  -e @/home/oyvind/.config/home-cluster/prosango-secrets.yml --ask-vault-pass
```

The playbook packages tracked and non-ignored new Django source files plus the
prebuilt frontend `dist`. It does not run npm/Rsbuild or install Node on the
target. Local `.env` files, virtualenvs, SQLite databases, uploads, and Python
caches are excluded. The Prosango checkout must contain the new environment,
PostgreSQL, Celery, and Gunicorn support; those changes need not be pushed for
this controller-source deployment.

Each bundle is extracted to `/srv/prosango/releases/<checksum>`, and
`/srv/prosango/current` points to the selected release. Python dependencies go in
`/srv/prosango/.venv`, while a mode-0600 `/srv/prosango/.env` holds runtime secrets.
PostgreSQL authentication is applied before `migrate --noinput`; then
`collectstatic --clear` copies Django and prebuilt frontend files to the persistent
static root. Gunicorn starts on `127.0.0.1:8000` and is health-checked before the
remaining Celery/Nginx configuration.

Collected static files are assigned to `www-data`. Nginx also joins the
application's read-only group so it can serve nested Django uploads, which may
use the application's primary group. Runtime secrets remain owner-only.

**Plan a maintenance window:** managed web/worker/Beat processes stop before code
or credentials change. Every code deployment recollects static files and starts
the application again. Failures can leave services stopped or partially
restarted; fix the failure and rerun. This is not a rolling or
automatic-rollback deployment. Old releases and uploads are retained; migrations
do not automatically transfer data from an existing SQLite database.

The `current` path must be absent or a symlink. An existing real directory is not
deleted or replaced automatically. Back up PostgreSQL and uploads before releases
that change the schema.
