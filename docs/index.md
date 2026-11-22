# Home cluster, one step at a time

Start with the machine you already have. Check that Ansible can reach it, learn
where the secrets live, and deploy one application. Only build installation
media when you actually need a new or deliberately reinstalled machine.

Like the Blakken guide, this follows short conversations rather than long
prompt templates. Commands explain what the AI is doing and let you run the
same step yourself. Example prompts here are suggestions, not records of
completed infrastructure work.

## Reading path

1. [Start small](apprentice/01-start-small.md): recognize the three jobs in this repo.
2. [First Ansible run](apprentice/02-first-ansible-run.md): inventory, ping,
   playbooks, and the target.
3. [Vault password file](apprentice/03-vault-password-file.md): unlock encrypted
   configuration without repeated prompts.
4. [Secrets script](apprentice/04-secrets-script.md): generate or replace one
   existing key.
5. [Minimal deployment](apprentice/05-minimal-deployment.md): one application
   on one host, starting with LAN HTTP.
6. [Next change](apprentice/06-next-change.md): update code without confusing
   it with provisioning or secret rotation.
7. [Build an ISO](apprentice/07-build-an-iso.md): prepare an unattended installer.
8. [Write the USB](apprentice/08-write-the-usb.md): identify the right device
   before using `dd`.
9. [Install and connect](apprentice/09-install-and-connect.md): boot deliberately,
   verify SSH, and return to Ansible.
10. [Take over](apprentice/10-take-over.md): transfer access and working state
    without transferring secrets into chat.

Commands assume this repository is your current directory unless noted.
Sugar already has infrastructure installed. The ISO path is optional and
destructive; it is not part of an ordinary app update.
