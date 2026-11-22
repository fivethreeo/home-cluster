# 2. Your first Ansible run

After [choosing the host](01-start-small.md), check the connection before
installing services.

The example request is:

> check that ansible can reach sugar

The direct command from this repository is:

```sh
ansible -i provision-hosts/inventory.yml sugar -m ping
```

Ansible's `ping` is not an ICMP network ping. It connects using the configured
transport and runs a small module on the host. The expected result includes
`"ping": "pong"`. This checks SSH and usable remote Python.

## Read the small command

`-i` selects the inventory. `sugar` selects the inventory host. `-m ping`
selects the module for this one-off operation.

Inspect available hosts without changing them:

```sh
ansible-inventory -i provision-hosts/inventory.yml --graph
```

`provision-hosts/inventory.yml` contains connection details and per-host service
switches. `provision-hosts/group_vars/all.yml` supplies disabled defaults.

If Ansible is not installed on your controller, use its normal package setup.
For a Debian controller, a basic installation is:

```sh
sudo apt install ansible python3-yaml
```

PyYAML is also needed by the secrets script later. Installing tools on the
controller is separate from installing services on Sugar.

## A playbook performs a sequence

For a fresh, approved host, provision its enabled services:

```sh
ansible-galaxy collection install -r deploy-app/requirements.yml
ansible-playbook -i provision-hosts/inventory.yml \
  provision-hosts/provision.yml --limit sugar
```

The collection file provides PostgreSQL/RabbitMQ modules also used in app
deployment. The playbook runs enabled infrastructure roles with privilege
escalation.

`--limit sugar` narrows this run; without it, the playbook targets its inventory
pattern, which is `all`. App roles can delegate work to separately configured
database/broker hosts even when the web host is limited.

Do not re-provision existing RabbitMQ just to practise the syntax. Package
upgrades require review and backup. A disabled role is skipped, not necessarily
uninstalled.

## Learn the other common flags

`--syntax-check` checks playbook syntax; it does not prove remote success.
`--check` asks supported tasks to predict changes; not every task supports it
fully, and it is not a universal safety guarantee. Avoid secret-bearing
`--diff` output.

`--ask-become-pass` is for sudo escalation when needed. The Vault password
unlocks encrypted input instead. They are not interchangeable.

Next: [The Vault password file](03-vault-password-file.md).
