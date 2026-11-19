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

## Encrypted S3 database and media backups

Backups are disabled by default. This feature supports Django, PostgreSQL and
`/srv/<app_name>/media/` on the same host, using local PostgreSQL peer authentication
as `postgres`. With backups enabled, the normal application deployment provisions
AWS resources, discovers project credentials, initializes an empty repository
and enables scheduled backups. Frontend-only applications
such as Enjoy have no database/media backup to enable.

All applications share the `safe-and-sound` S3 bucket in Stockholm (`eu-north-1`),
with an independent encrypted restic repository per application: `prosango/`,
`template/`, and so on. Never point two applications at the same repository.
Use a globally unique bucket name if `safe-and-sound` is unavailable, and set
`app_backup_bucket` to that name for every application. Each application's
credentials need `s3:GetBucketLocation` on the bucket, `s3:ListBucket` and
`s3:ListBucketVersions` restricted to its prefix and descendants, and
`s3:GetObject`, `s3:PutObject`, and `s3:DeleteObject` on that prefix's objects.
Deletion is required for locks and retention. Interactive AWS SSO can provision
these resources but is not sufficient for unattended scheduled backups.
Do not configure S3 lifecycle expiration of restic objects: deleting individual
objects outside restic can corrupt retained snapshots.

### AWS resources managed by deployment

The controller needs an installed, authenticated AWS CLI with permission to
provision CloudFormation, S3, IAM access keys and Secrets Manager resources
(SSO is suitable). Select the intended AWS account before deployment.
`app_backup_provision_aws` defaults to `true`: enabling backups lets the normal
`deploy.yml` or `deploy-code.yml` invocation create/update the shared bucket
stack and the application's project stack. No separate CloudFormation command
or manually copied secret ARN is required.

The shared stack defaults to `home-cluster-backups`; project stacks default to
`home-cluster-backup-<app_name>` (underscores become hyphens). All projects sharing
the bucket must use the same bucket name, region and shared stack name.
Override `app_backup_bucket_stack_name` or `app_backup_project_stack_name` only
to select an existing stack or deliberately establish separate ownership.
Normal redeploys leave unchanged resources and repositories intact.

For reference, the equivalent optional manual provisioning commands are:

```sh
backup_region=eu-north-1
backup_bucket=replace-with-your-globally-unique-name
aws cloudformation deploy \
  --region "$backup_region" --stack-name home-cluster-backups \
  --template-file deploy-app/aws/backup-bucket.yml \
  --parameter-overrides BucketName="$backup_bucket"

aws cloudformation deploy \
  --region "$backup_region" --stack-name home-cluster-backup-prosango \
  --template-file deploy-app/aws/backup-project.yml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides BucketName="$backup_bucket" ProjectName=prosango
```

`ProjectName` must equal `app_name`; prefixes are always `<app_name>/`, never
the bucket root or a shared parent. Repeat the project command with a different
stack name and `ProjectName` for each application. Prefixes need no folder
creation: `restic init` writes the repository objects.
`app_backup_repository`, if overridden, must still match the configured endpoint,
bucket and application name exactly.

The bucket blocks all public access, disables ACLs, uses server-side AES256
encryption in addition to restic encryption, and denies insecure transport.
Versioning is enabled, and both the bucket and its HTTPS-only policy are retained
when the stack is deleted or replaced. Incomplete multipart uploads are aborted
after seven days; no completed objects or historical versions expire.
The project identity can only list its prefix and read/write/delete its objects
(including multipart cleanup); it cannot delete historical object versions or
manage the bucket. Do not attach broader policies to these dedicated users.

The project stack automatically creates an IAM user without console access, an
active access key, and a Secrets Manager secret encrypted with the AWS-managed
`aws/secretsmanager` KMS key. Neither the access key nor its secret value appears
in stack outputs. Retrieve only the secret ARN:

```sh
aws cloudformation describe-stacks \
  --region "$backup_region" --stack-name home-cluster-backup-prosango \
  --query 'Stacks[0].Outputs[?OutputKey==`CredentialsSecretArn`].OutputValue | [0]' \
  --output text
```

Deployment discovers this ARN automatically. When AWS provisioning is disabled,
set `app_backup_aws_secret_id` to the ARN in the application's non-secret YAML.
Ansible uses the controller's authenticated AWS CLI to fetch
the current secret in `app_backup_region`. Give that controller identity
`secretsmanager:GetSecretValue` on only the relevant secret ARN. The backup IAM
user does not need Secrets Manager permissions, and the application host does not
need AWS CLI authentication.

Retrieval and configuration writing suppress Ansible output. Credentials remain
in controller memory and are written only into the existing root-owned `0600`
backup configuration on the application host, not a plaintext controller file
or Vault import. A failed retrieval, malformed secret, or mismatching project
or bucket stops deployment; it never falls back to stale Vault credentials.
Do not enable AWS CLI debug logging or print registered secret results.
Keep the independent restic encryption password in Ansible Vault.

Creation is automatic, but scheduled key rotation is not configured. To rotate,
set `app_backup_access_key_serial` to a higher positive integer in the application's
external YAML and redeploy. An empty value (the default) preserves an existing
stack's serial, or uses the template's default for a new stack.
CloudFormation replaces the key and updates the secret; it does not update
application hosts by itself; the deployment then fetches the new credentials
and installs them. Pause backup scheduling before a rotation deployment and
verify a backup afterwards. This is not zero-downtime rotation: the old key is
revoked during the stack update, before host configuration is refreshed.
Unmanaged keys can consume the IAM user's key quota and prevent
creation/replacement; manage them separately.
Prefer short-lived credentials through an unattended identity mechanism when
available; the current runner requires explicit keys.

Deleting the project stack revokes its managed access key and removes the user,
but retains the encrypted secret for explicit administrative cleanup. Retained
secret versions may contain revoked keys; they are not usable recovery credentials.
Retained secrets and secret retrieval incur
[Secrets Manager charges](https://aws.amazon.com/secrets-manager/pricing/).

These templates create **new** resources, not imports of an existing bucket.
For a bucket managed outside this stack, set `app_backup_manage_bucket: false`
and configure equivalent bucket protections separately; deployment still manages
the project stack. Set `app_backup_provision_aws: false` to manage all AWS resources
externally. Changing the bucket name or project name creates a new
destination; it does not migrate backups. Keep the old repository and recovery
password until migration and restore verification are complete.

Versioning is an extra recovery layer, not immutable storage: administrators can
still delete versions, and recovering a deleted restic repository may require
restoring multiple related objects. Restic pruning creates delete markers and
**does not reclaim historical-version storage**. Review version storage growth
and plan any historical-version cleanup deliberately; never expire live restic
objects. Storage, requests, retained versions and restore data transfer incur
[S3 charges](https://aws.amazon.com/s3/pricing/).

For production operation, enable S3 server access logging to a separate encrypted
log bucket, CloudTrail S3 data events and CloudWatch request metrics/alarms.
Those are not provisioned here and can add charges. Encrypt CloudTrail and
CloudWatch log destinations with KMS; scope service delivery policies with
`aws:SourceAccount`/`aws:SourceArn`. See
[S3 security best practices](https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html).

### Configure and run application backups

Add these non-secret settings to each application's external YAML:

```yaml
app_backup_enabled: true
app_media_enabled: true
app_backup_bucket: safe-and-sound
app_backup_region: eu-north-1
app_backup_schedule: "*-*-* 03:00:00"
app_backup_retention_days: 14
```

Use `ansible-vault edit` to add `app_backup_password` (an independently generated
random password of at least 32 characters) to each application's external
encrypted secrets file. Keep an independent recovery copy of the restic password;
losing it makes backups unrecoverable. Do not put credentials in command arguments
or Git. `update-secret.py` cannot add previously absent keys.

For existing deployments using externally managed keys, set
`app_backup_provision_aws: false`, leave `app_backup_aws_secret_id` empty and
supply `app_backup_aws_access_key_id` and
`app_backup_aws_secret_access_key` through Vault instead. Setting a secret ARN
takes precedence over those legacy values.

Run the normal code deployment with those application settings and the encrypted
restic password. Backup setup is part of deployment, not a separate step:

```sh
ansible-playbook -i provision-hosts/inventory.yml deploy-app/deploy-code.yml \
  --limit sugar -e @app.yml -e @secrets.yml --ask-vault-pass
```

The infrastructure-only `deploy-app/deploy.yml` also performs backup setup.
To configure only backups without redeploying application services, the dedicated
playbook remains available:

```sh
ansible-playbook -i provision-hosts/inventory.yml deploy-app/backup.yml \
  --limit sugar -e @app.yml -e @secrets.yml --ask-vault-pass
```

Deployment inspects at most one object version or delete marker under the
project prefix, using its own prefix-scoped credentials. Only a completely empty
prefix is initialized. Existing objects, historical versions or delete markers
prevent initialization; deployment verifies the repository and password instead.
A missing config in a nonempty prefix, wrong password, or access/connectivity
failure stops deployment rather than creating a replacement repository.
Recovery deployments never initialize a new empty repository.

Initialization and password verification complete before the timer is enabled.
Check mode does not create/update AWS stacks or initialize repositories; it
requires an existing project stack and access to its secret.

After deployment, run and inspect the first backup. These commands run on the
application host; replace `prosango` for other applications:

```sh
sudo systemctl start application-backup-prosango.service
sudo ansible-app-backup /etc/ansible-app/prosango/backup.json restic snapshots
sudo ansible-app-backup /etc/ansible-app/prosango/backup.json restic check --read-data
sudo systemctl list-timers application-backup-prosango.timer
sudo journalctl -u application-backup-prosango.service
```

Backups run daily at approximately 03:00 host time with up to 15 minutes' jitter
and catch up after downtime.

Each run exports and validates a custom-format PostgreSQL dump, then creates one
encrypted snapshot containing that dump and media. Only a successful complete
backup triggers app-scoped `forget --keep-within 14d --prune`. This retains roughly
14 days relative to the newest snapshot, not an absolute object-expiration date;
failed/partial backups do not prune history. Budget local space for one full
database dump and the restic cache under `/var/lib/ansible-app-backup/<app_name>/`.
The private plaintext dump is removed on success, failure and handled shutdown;
forced termination or a host crash can leave it until the next run. Concurrent
backups of the same application are rejected.

Snapshots are marked `app-backup-complete` only after a fully successful upload.
Automated listing, restore and retention require that marker; partial uploads
cannot masquerade as complete recovery points. Snapshots left with `pending-*`
tags after failures remain available for manual diagnosis/removal and are not
automatically pruned.

The database dump is transactionally consistent, but media is read live. Quiesce
application and worker writes for strict cross-database/media consistency.
Code, PostgreSQL global roles, environment files and credentials are not included;
retain those separately and recreate infrastructure before disaster recovery.
Monitor failed systemd services and snapshot age externally; this feature does
not send failure notifications or replace regular restore drills.
Setting `app_backup_enabled: false` and rerunning the playbook disables the timer
without deleting credentials, cached state or S3 snapshots.

### Restore to staging first

On the application host, choose a snapshot from `restic snapshots` and restore
to a new empty directory, never directly onto live data:

```sh
sudo install -d -m 0700 /var/tmp/prosango-restore
sudo ansible-app-backup /etc/ansible-app/prosango/backup.json \
  restic restore SNAPSHOT_ID --target /var/tmp/prosango-restore
sudo -u postgres createdb -O prosango prosango_restore
sudo cat /var/tmp/prosango-restore/var/lib/ansible-app-backup/prosango/database.dump \
  | sudo -u postgres pg_restore --exit-on-error --no-owner --role=prosango \
      --dbname=prosango_restore
```

Use a unique, nonexistent staging database name and an existing application
database role (adjust `prosango` if `app_database_user` differs). The pipe lets
PostgreSQL read the archive without making the root-only restore tree public.
Verify database records and the restored `srv/prosango/media/` files before any
live recovery. Plan downtime, stop web/worker writes, and restore database and
media together. Preserve application ownership and `www-data` group/read
permissions when copying media back; never run destructive live replacement
commands without a separately verified recovery plan.

### List or deploy-and-restore with Ansible variables

For applications with the backup runner/configuration already installed, list
snapshots without deploying code, installing packages, or changing services:

```sh
ansible-playbook -i provision-hosts/inventory.yml deploy-app/backup.yml \
  --limit sugar -e @app.yml -e app_backup_list=true
```

Listing uses the protected credentials already on the host; it does not need
the controller's Vault secrets. `app_backup_list=true` also works with
`deploy-code.yml` and exits before source packaging or deployment. Snapshot
listing and restoration are mutually exclusive.

To deploy code and automatically replace the live database and media from one
selected snapshot, supply its ID as a one-off extra variable:

```sh
ansible-playbook -i provision-hosts/inventory.yml deploy-app/deploy-code.yml \
  --limit sugar -e @app.yml -e @secrets.yml --ask-vault-pass \
  -e app_restore_snapshot=SNAPSHOT_ID
```

**This intentionally replaces live data.** Use an explicit snapshot ID from the
list (at least eight hexadecimal characters), not `latest`. Do not save
`app_restore_snapshot` in ordinary deployment configuration: every invocation
with it requests a new recovery. AWS resources and an initialized repository
must already exist. Recovery does not require enabling nightly backups.

The deployment downloads and verifies the app's complete snapshot before
stopping managed application processes and backup timers. It deploys the
selected code, imports the dump into a separate database as the application
role, prepares media with application ownership and Nginx-readable permissions,
then swaps them into place before running migrations and health checks.
Workers restart only after data restoration and migrations succeed.
By default, queued tasks in the deployed Celery app's configured queues are
discarded before services restart; opt out explicitly with
`-e app_restore_discard_queued_tasks=false` if those jobs should survive.
A fresh, persistent app-specific cache namespace prevents stale Memcached
entries from resurfacing without flushing other applications' caches.

The previous database is retained as `<database-prefix>_before_<token>` with
connections disabled; previous media remains at
`/srv/<app_name>/.media-before-<token>`. Downloads and a `restore.json` recovery
manifest remain in the private staging directory printed by Ansible.
Allow disk space for downloaded media, a replacement media tree and another
database, in addition to the retained original data. Remove these recovery
copies manually only after verification.

Failed imports leave the live database/media untouched. A failure during
cutover or migrations leaves web/worker processes stopped, with recovery data
retained for operator inspection; it does not blindly restart inconsistent
data or automatically roll back. Database and filesystem changes are not one
atomic transaction. This workflow supports colocated Django/PostgreSQL/media
and TypeScript components, full code deployments only, and not `--check`.

## DNS and certificates

Both features are opt-in and use Domeneshop credentials stored root-only on the
web host. DDNS updates `ddns_domains` (default: `app_domains`) immediately and
hourly by default via a per-application systemd timer. Set `ddns_update_interval`
to change the interval, for example `6h`. Domeneshop detects the requesting
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
ansible-playbook -i provision-hosts/inventory.yml deploy-app/backup.yml --syntax-check
```

The backup integration test uses a disposable, socket-only local PostgreSQL
cluster and a filesystem restic repository; it never contacts AWS or deployed
services. It runs when `restic`, `initdb` and `pg_ctl` are on `PATH` under a
non-root user, otherwise it skips. `BACKUP_TEST_PG_SHARE` can identify the
PostgreSQL share directory when testing extracted, uninstalled server binaries.

## Deploy Prosango or Template code and prebuilt assets

Use `deploy-code.yml` to deploy either sibling checkout. `app_django_module`
defaults to `prosango`; set it to `template` for `../template-project`.
The default source directory is the sibling `<app_django_module>-project`.
This also selects Gunicorn's WSGI module and Celery's default app, independently
of `app_name` (which may be `template_staging`, for example).
When frontend is enabled (the default with Django), build it yourself beforehand,
using the same checkout/branch as the Django code:

```sh
cd ../prosango-project
python run.py npm ci
python run.py npm run build
cd ../ansible-home-cluster
```

Example `app.yml` for IP-only hosting:

```yaml
app_name: prosango
app_django_module: prosango
app_domains: ["192.168.1.100"]
app_source_dir: ../prosango-project
app_frontend_dist: ../prosango-project/typescript-frontend/dist
app_media_enabled: true
celery_enabled: true
celery_beat_enabled: true
ddns_enabled: false
certbot_enabled: false
```

For Template, use this instead:

```yaml
app_name: template
app_django_module: template
app_domains: ["192.168.1.100"]
app_media_enabled: true
celery_enabled: true
celery_beat_enabled: true
app_ts_backend_enabled: true
app_ts_ws_backend_enabled: true
ddns_enabled: false
certbot_enabled: false
```

Build from `../template-project` in this case, including both enabled TypeScript
backends using the commands below. Use a separate `template-secrets.yml` Vault.
If both apps share a server, give them distinct public domains and backend ports
(including `app_bind`), users, database/broker namespaces and app names. Reusing
the same IP-only `app_domains` on both apps does not select between Nginx sites.

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
selected prebuilt frontend `dist`. It does not build frontend or backend assets on the
target; Node is installed only for enabled TypeScript services. Local `.env`
files, virtualenvs, SQLite databases, uploads, and Python
caches are excluded. The selected checkout must contain the environment,
PostgreSQL, Celery, and Gunicorn support; those changes need not be pushed for
this controller-source deployment.

Each bundle is extracted to `/srv/prosango/releases/<checksum>`, and
`/srv/prosango/current` points to the selected release. Python dependencies go in
`/srv/prosango/.venv`, while a mode-0600 `/srv/prosango/.env` holds runtime secrets.
PostgreSQL authentication is applied before `migrate --noinput`; then
`collectstatic --clear` copies Django and prebuilt frontend files to the persistent
static root. Gunicorn starts on `127.0.0.1:8000` and is health-checked before the
remaining Celery/Nginx configuration.
With frontend enabled, Prosango requires its editor manifest as well as the main frontend manifest;
Template's frontend has no editor and needs only `dist/manifest.json`.

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

## Optional Prosango Memcached cache

Provision Memcached on the web host first with `memcached_enabled: true` in the
infrastructure inventory. Then add this to the application deployment variables:

```yaml
app_memcached_enabled: true
# Default; change only if the cache uses another port/address.
app_memcached_location: 127.0.0.1:11211
```

Run `deploy-code.yml` to install the Python client and configure Django's default
cache as `PyMemcacheCache`. Deployment verifies an actual cache write/read/delete
before migrations. The service is localhost-only with UDP disabled; no new
secrets or public ports are required.

Each deployment uses `app_name` as its cache-key prefix, so `prosango` and
`prosango_staging` can share one Memcached service without key collisions.
Prefixes are not a security boundary between applications. Cache data is
temporary and can be evicted or lost on restart; it is not a database.

Application code uses `django.core.cache.cache`; configuring the backend does
not automatically cache all views or switch sessions to cache storage.
`app_memcached_enabled` defaults to false. Disabling it and redeploying restores
Django's local-memory cache without stopping the shared Memcached service.

## Selectable Django, frontend, TypeScript HTTP and WebSocket components

`deploy-code.yml` deploys Django by default. Enable the new services independently:

```yaml
app_django_enabled: true
app_ts_backend_enabled: true
app_ts_ws_backend_enabled: true
app_django_location: /
app_ts_backend_location: /api/
app_ts_ws_backend_location: /ws/
app_bind: 127.0.0.1:8000
app_ts_backend_port: 8001
app_ts_ws_backend_port: 8002
```

Build the selected services locally before deploying:

```sh
cd ../prosango-project
python run.py npm-backend ci
python run.py npm-backend run build
python run.py npm-ws-backend ci
python run.py npm-ws-backend run build
cd ../ansible-home-cluster
```

Frontend is optional for Django. For a checkout supporting
`DJANGO_FRONTEND_ENABLED` (such as Template), use:

```yaml
app_django_enabled: true
app_frontend_enabled: false
```

Only Django source is packaged; no frontend directory, build or manifest is
required. Django still collects its admin and other app static files. The runtime
receives `DJANGO_FRONTEND_ENABLED=false` so it does not read a stale manifest.
With frontend enabled, missing or development manifests still fail packaging.
Component switches are explicit, not inferred from missing source directories.

Code deployments enable the Celery worker with Django by default and reject
mismatched `celery_enabled`/`app_django_enabled` switches. Beat remains optional
and requires Django.

Node-only deployment does not require frontend assets or Django/database secrets.
For example, set `app_django_enabled: false` and enable either TypeScript component.

To serve the prebuilt frontend and its HTML without Django:

```yaml
app_django_enabled: false
app_frontend_enabled: true
app_frontend_html_location: /template/
celery_enabled: false
celery_beat_enabled: false
app_static_location: /template/static/
```

Enable either TypeScript component as needed; frontend-assets-only deployment
also works without backend services. `app_frontend_enabled` defaults to the
Django switch, preserving existing deployments; override it to disable frontend.

`app_frontend_html_enabled` defaults to true when frontend is enabled and Django
is disabled. Nginx serves `dist/index.html` at `app_frontend_html_location`, which
defaults to `app_django_location`. A missing built HTML file fails deployment
before the new frontend release is selected. Shared-site deployments must use a
non-root page location, such as `/template/`. The page uses an exact location,
not an SPA fallback, and does not interfere with nested static/API/WS routes.

Build `typescript-frontend/dist` locally first. Without Django, the playbook
uploads it into the application's immutable release and Nginx serves its files
directly under `app_static_location`, retaining the dist-relative directory
layout. It does not install Python dependencies, migrate or configure a database
or broker, or run `collectstatic`. No Django page or backend URL data attributes
are created.

Template uses `typescript-frontend/src/index.html` as its editable HTML template.
Its build inserts the CSS/JavaScript tags automatically. Build with an asset
prefix matching `app_static_location`; for the example above:

```sh
cd ../template-project
RSBUILD_ASSET_PREFIX=/template/static/ python3 run.py npm run build
```

For assets only with HTML hosted elsewhere, set
`app_frontend_html_enabled: false`. This disables the page location but still
uploads dist files, including any built HTML, under the asset prefix.

On an existing application, disabling Django still stops its Django/Celery
services and removes those routes; it does not leave Django running unchanged.

### Update only frontend while keeping backends running

Use the existing application's configuration with `app_frontend_only_update:
true` instead of disabling Django or Node services:

```sh
ansible-playbook -i provision-hosts/inventory.yml deploy-app/deploy-code.yml \
  --limit sugar -e @/home/oyvind/.config/home-cluster/template.yml \
  -e app_frontend_only_update=true
```

This selects only the frontend bundle and skips backend provisioning,
environment changes, migrations, dependency installs and service stops/restarts.
It does not require database/broker secrets. Frontend releases live separately
under `/srv/<app>/components/frontend/`, leaving Django and Node release links
unchanged. Assets merge into the existing collected static directory without
clearing admin assets, older hashed files or Django's manifest.

HTML serving defaults to enabled for this update mode too. The exact page
location can share Django's page prefix, such as `/template/`, while nested admin,
media, API and WebSocket routes keep their existing configuration. Set
`app_frontend_html_enabled: false` to update assets while retaining Django's page.
Only this application's Nginx configuration is updated and Nginx is reloaded.
A later full-stack deployment uses its normal HTML and service switches again.

The switches describe the desired stack: disabled components are stopped and
disabled, and their Nginx routes are removed. Other apps are untouched. Node
releases live under `/srv/<app>/components/<component>/`; systemd units are named
`typescript-backend-<app>` and `typescript-ws-backend-<app>`. Application code is
never compiled on the target; only `npm ci --omit=dev --ignore-scripts` runs there.

Locations must be distinct slash-terminated prefixes. Nginx strips the selected
HTTP/WebSocket prefix before proxying, so `/api/healthz` reaches `/healthz` and
clients connect at `ws://<domain>/ws/` (or `wss://` with TLS). Non-root prefixes
have a slash-adding redirect; use the trailing slash directly for WebSockets.
Django can also use `/django/`; deployment sets its script prefix so reversed
admin URLs remain correct. Static/media default to `/static/` and `/media/`.

### Template beside Prosango on the same IP

To attach Template to an existing Prosango Nginx site rather than create a
second virtual host with the same IP/server name, use:

```yaml
app_name: template
app_django_module: template
app_domains: ["192.168.1.100"]
app_nginx_shared_site: prosango
app_django_location: /template/
app_bind: 127.0.0.1:18000
app_static_location: /template/static/
app_media_location: /template/media/
app_session_cookie_name: template_sessionid
app_csrf_cookie_name: template_csrftoken
app_cookie_path: /template/
app_ts_backend_enabled: true
app_ts_backend_location: /template/api/
app_ts_backend_port: 18001
app_ts_ws_backend_enabled: true
app_ts_ws_backend_location: /template/ws/
app_ts_ws_backend_port: 18002
ddns_enabled: false
certbot_enabled: false
```

The existing site must already be installed. Deployment adds a backed-up
include line and writes only Template's locations under
`/etc/nginx/app-locations/prosango/template.conf`; it does not replace Prosango's
routes or restart its application processes. This checkout's standalone site
template retains the include on later Prosango deployments.

Shared-site deployments require non-root route prefixes, separate static/media
locations, and disabled per-app Certbot/DDNS. Template reads the generated
static/media URL and cookie settings; older Prosango versions do not implement
these isolation settings. `/template` redirects to `/template/`. Each app must
also use its own name, database, broker namespace and unoccupied backend ports.

Django's environment also receives `DJANGO_TS_BACKEND_URL` and
`DJANGO_TS_WS_BACKEND_URL`: the enabled components' public location prefixes,
or empty values when disabled. These appear as `data-ts-backend-url` and
`data-ts-ws-backend-url` on public-page bodies. They refer to the gateway paths,
not private backend servers, and work with custom locations and gateway TLS.

### Optional internal authentication and notifications

For Template's internal features, add these optional keys to the encrypted
application secrets file:

```yaml
django_internal_auth_token: ""
ws_internal_notify_token: ""
```

Generate them with `update-secret.py <app_name> django_internal_auth_token`
and `update-secret.py <app_name> ws_internal_notify_token`, then redeploy.
Deployment passes the shared auth key to Django and both Node processes, and
the notification key to Django/Celery and both Node processes when WS is enabled.
`WS_INTERNAL_URL` points each sender directly at the WS host/port, using its local
bind address on the same host and its advertised private address otherwise.
Template's `template.tasks.notify` uses these settings for authenticated Celery
notifications. Disabling WS clears its sender URL and token.
Runtime environment files are
mode 0600 and their template contents are hidden from Ansible logs. Prosango
versions without these features simply ignore the extra environment variables.
Omitted/empty keys leave those features disabled and produce deployment/startup
warnings; anonymous HTTP and WebSocket access remains available.

Node's `DJANGO_INTERNAL_URL` targets Gunicorn on the same host or the private
Django origin on a separate host, without the public Django mount prefix.
These destination hosts are included in Django's allowed hosts. WS processes
receive `WS_ALLOWED_ORIGINS` from `app_domains`, using HTTP or HTTPS according
to `certbot_enabled`. Override `app_ws_allowed_origins` with a list of exact
browser origins if external TLS termination or nonstandard public ports differ.

The public Nginx gateway returns 404 for `<django-prefix>internal/` and
`<backend-prefix>internal/`. Trusted senders must call `/internal/notify/`
directly on the WS service's private host/port, using the notification key;
do not use the public `/ws/` gateway path.

Django code deployments automatically store Celery results in the application's
PostgreSQL database, using its database host, user and Vault password. No
`celery_result_backend` URL is needed in the application configuration.
Override `celery_result_backend` for a different store, or explicitly set
`celery_result_backend: ""` to disable result storage. Infrastructure-only and
Node-only deployments do not enable result storage automatically.

### Separate servers behind one Nginx gateway

Run the playbook with `--limit` selecting only the gateway. Component hosts must
still be listed in the same inventory; tasks delegate to them:

```yaml
all:
  hosts:
    gateway:
      ansible_host: 192.168.1.100
      nginx_enabled: true
    django:
      ansible_host: 192.168.1.101
      postgresql_enabled: true
      rabbitmq_enabled: true
    http:
      ansible_host: 192.168.1.102
    websocket:
      ansible_host: 192.168.1.103
```

Example app variables:

```yaml
app_name: prosango
app_domains: [example.com]
app_django_enabled: true
app_django_host: django
app_django_origin_bind: 192.168.1.101
app_django_origin_port: 8080
app_django_location: /django/
app_ts_backend_enabled: true
app_ts_backend_host: http
app_ts_backend_bind_host: 192.168.1.102
app_ts_backend_location: /api/
app_ts_ws_backend_enabled: true
app_ts_ws_backend_host: websocket
app_ts_ws_backend_bind_host: 192.168.1.103
app_ts_ws_backend_location: /ws/
```

Provision the inventory first, then deploy with `--limit gateway`. Remote
components require explicit private IPv4 bind addresses; the gateway defaults
to contacting those same addresses. Override `app_django_address`,
`app_ts_backend_address`, or `app_ts_ws_backend_address` if it needs different
contact addresses.

Django keeps Gunicorn on localhost. Deployment installs a private Nginx origin
on its host to serve Django, collected static files and persistent uploads;
the gateway proxies those paths to it. By default PostgreSQL/RabbitMQ are also
on the Django host. Existing separate database/broker settings remain supported.
Celery runs beside Django. Memcached, if enabled, must be reachable from Django,
not merely from the gateway.
The private Django origin is also installed when Django runs on the gateway
but a Node backend runs elsewhere. Set `app_django_origin_bind` to Django's
private IPv4 address for this layout, too.

Public TLS/DDNS stay on the gateway. Internal traffic is HTTP: use a trusted
private network and firewall origin/backend ports for the gateway and explicitly
trusted service hosts. Node hosts need the Django origin for session checks;
trusted notification senders need the WS service port. Prefixes and private
addresses are not authentication. Only expose public
80/443; SSH and other service ports should be restricted appropriately.

Before moving a component to a new inventory host, disable it using its old
host assignment first. Deployment does not discover or remove services left on
previous hosts. Keep per-app ports distinct when staging shares a host.
