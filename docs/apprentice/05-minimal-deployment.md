# 5. Deploy one minimal app

Now [the secrets exist](04-secrets-script.md). Start with one app on one host
over LAN HTTP. Do not add domains, DDNS, certificates, extra backends, or cloud
backup provisioning just to see whether the basic deployment works.

This chapter uses `template`, with a matching `../template-project` checkout.
If that source is unavailable, choose a real matching application/module
instead. Do not pretend the example checkout exists.

## The small configuration

Create `~/.config/home-cluster/template.yml` outside the repo:

```yaml
app_name: template
app_django_module: template
app_domains:
  - 192.168.1.100
app_frontend_enabled: false
app_ts_backend_enabled: false
app_ts_ws_backend_enabled: false
celery_beat_enabled: false
ddns_enabled: false
certbot_enabled: false
app_backup_enabled: false
```

This selects Django without frontend or TypeScript services. Code deployments
pair Django with the Celery worker; they do not support turning that worker
off independently. PostgreSQL and RabbitMQ therefore still need provisioning
and app passwords. Beat is optional and stays off here.

The default source path is derived from `app_django_module`, so this selects
`../template-project`. Set `app_source_dir` explicitly when the checkout is
elsewhere. The module must match its `wsgi.py`.

Do not apply this tutorial file over an existing production application's
configuration. LAN HTTP is for the initial trusted-network check, not a plan
for exposing logins to the internet.

## Deploy code, not just resources

Suggested prompt:

> deploy template on sugar, keep it minimal

The AI should use `deploy-code.yml`, with the protected app config, encrypted
secrets, and Vault password file:

```sh
ansible-galaxy collection install -r deploy-app/requirements.yml
ansible-playbook -i provision-hosts/inventory.yml deploy-app/deploy-code.yml \
  --limit sugar \
  -e @"$HOME/.config/home-cluster/template.yml" \
  -e @"$HOME/.config/home-cluster/template-secrets.yml" \
  --vault-password-file "$HOME/.config/home-cluster/vault-password"
```

Append `--syntax-check` first if checking the command/configuration before the
actual run. Syntax success is not deployment success.

`deploy.yml` configures resources but does not install application code/runtime.
`deploy-code.yml` includes the app-resource work and installs the selected code,
dependencies, migrations, runtime settings, and services. You do not need both
invocations for this code deployment.

## Check the result

Look for zero failed tasks, then check the page at `http://192.168.1.100/`
and the relevant services/logs. The example Django service is
`gunicorn-template`; its worker is `celery-template`.

If another app already occupies the same host/domain/root route, stop and
choose compatible domain/path/port settings first. "Minimal" does not mean
overwriting a working application.

Next: [Make the next change](06-next-change.md).
