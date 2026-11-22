# 1. Start small

You do not need to build a cluster before doing something useful with this
repository. Start with one host, one application, and one result you can check.

An example opening request is:

> check that ansible can reach sugar

That is enough to begin when the AI has this repository as context. It should
read the inventory, check the configured connection, and report the result.
It should not interpret the request as permission to reinstall the machine.

## Three jobs, not one giant install

| Directory | What it does |
| --- | --- |
| `generate-isos/` | Builds a Debian installer configured for a particular host |
| `provision-hosts/` | Installs shared infrastructure services on Debian hosts |
| `deploy-app/` | Creates app resources and optionally deploys source/services |

An ISO is useful for a fresh machine. Provisioning prepares PostgreSQL,
RabbitMQ, Nginx, and optional Memcached. Application deployment configures a
particular application on that prepared host.

These are different operations. Building another ISO does not update the
machine already running. Deploying application code does not mean reinstalling
Debian.

## Start with the established host

The checked-in inventory contains `sugar` at `192.168.1.100`, with PostgreSQL,
RabbitMQ, and Nginx enabled. Its SSH username and private-key path are configured
there too.

Those values describe the current example, not a universal home network.
On another workstation, confirm the key path and authorized access. Do not
copy someone else's private key into the repository.

Sugar already has infrastructure installed. Read the next chapter and try a
connection check first. Re-provisioning RabbitMQ can upgrade packages, so it
is not a harmless connectivity test.

## Keep the prompts about the next thing

Suggested prompts:

> check the inventory

> just sugar

> what needs to be installed first?

The AI should find the files and distinguish what is missing from what already
works. You do not need to list every role variable in a first message.

If the target is unclear, resolve it before changing a host. Short prompts
work because the conversation carries context, not because target selection
stops mattering.

Next: [Your first Ansible run](02-first-ansible-run.md).
