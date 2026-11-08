# Host provisioning

Provision one or more Debian hosts with RabbitMQ, PostgreSQL, and Nginx:

```sh
ansible-playbook -i inventory.yml provision.yml
```

Services are selected independently per host. A host can run every service:

```yaml
all:
  hosts:
    sugar:
      ansible_host: 192.168.1.100
      rabbitmq_enabled: true
      postgresql_enabled: true
      nginx_enabled: true
```

RabbitMQ uses Team RabbitMQ's signed `deb1.rabbitmq.com` and
`deb2.rabbitmq.com` APT repositories for the host's Debian release (amd64), as
in the original shell installer. Provisioning upgrades an installed RabbitMQ
package to the latest available version; review upstream upgrade requirements
and back up broker data before rerunning it on an existing server.

Provisioning installs and starts infrastructure services only. Application Linux
users, PostgreSQL databases/users and `pg_hba.conf` rules, RabbitMQ vhosts/users,
Nginx sites, Domeneshop DDNS, and DNS-01 certificates belong to `deploy-app/`.
Configure `ddns_enabled` and `certbot_enabled` in deployment variables, not here.
Previously installed Certbot packages, certificates, and timers are not removed.
