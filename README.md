# Home cluster

This repository builds unattended Debian installer ISOs, provisions host services,
and deploys applications.

The [apprentice guide](docs/index.md) starts small and follows the conversational
way of working: ask for the next change, let the AI inspect the existing
procedure, check the result, and continue. It covers Ansible, the Vault password
file, `update-secret.py`, and writing installer media with `dd`.

For detailed reference:

- [Existing ISO-to-Prosango walkthrough](DEPLOYMENT.md)
- [ISO builder](generate-isos/README.md)
- [Host provisioning](provision-hosts/README.md)
- [Application deployment](deploy-app/README.md)

Build or serve the guide from this repository:

```sh
python3 -m venv .venv-docs
.venv-docs/bin/python -m pip install -r requirements-docs.txt
.venv-docs/bin/python -m mkdocs build --strict
.venv-docs/bin/python -m mkdocs serve
```

Generated docs are in the ignored `site/` directory. This is a docs build, not
an infrastructure run. Reading the installation chapter does not authorize
erasing a USB or reinstalling a host.
