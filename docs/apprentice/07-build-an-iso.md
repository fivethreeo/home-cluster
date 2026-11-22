# 7. Build an installer ISO

[Updating an app](06-next-change.md) does not require this chapter. Use it only
for a new machine or an intentionally authorized reinstall.

An ISO here is an unattended Debian netinst installer, not a live desktop,
a backup, or a rescue image. Booting it can erase a disk without asking you
the usual installation questions.

Suggested prompt:

> build an iso for the new host

The AI should first identify the host and network settings. Building an ISO
is not permission to boot it on an existing server.

## What goes into it

Read `generate-isos/iso-builds.yml`. Each host has a hostname, IP suffix, and
network group. The current Sugar definition combines prefix `192.168.1` and
suffix `100` into `192.168.1.100`.

The group contains the netmask, gateway, and DNS servers. Common settings include
the login username, package list, and SSH public-key path.

The ISO puts the **public** key into the installed user's authorized keys.
Do not embed your private key, app Vault, or Vault password file into an image.
Extra initrd files are copied into the installer; only include material you
intend to distribute with it.

The default packages include Python, SSH, sudo, and Ansible. The installed
user has passwordless sudo and no usable password-based login from the
preseeded password setting. Keep the corresponding private key accessible
through approved channels or you can lock yourself out.

## Build from the ISO directory

```sh
cd generate-isos
ansible-galaxy collection install -r requirements.yml
ansible-playbook build-isos.yml --ask-become-pass
cd ..
```

This local playbook installs build dependencies and mounts OverlayFS with sudo.
The become password here is not the Ansible Vault password. The ISO builder
does not require application Vault secrets.

The builder downloads the current Debian amd64 netinst image, prepares
host-specific overlays/preseed files, and writes generated output under:

```text
generate-isos/output/sugar-debian-preseed.iso
generate-isos/output/sugar-debian-preseed.iso.md5sum
```

The output name follows the configured hostname. The playbook builds all
configured `iso_builds`; `--limit sugar` does not select one image because
this play runs on `localhost`.

## Check before moving on

Verify the intended hostname/IP, key, and output file. A rebuilt ISO does not
update an already-installed host. The generated MD5 checksums help detect a
changed output file; they are not a signature proving a trusted upstream image.

Most importantly, the installer selects and erases the first detected disk,
including existing LVM/RAID layouts. Do not assume it knows which disk you
want to preserve.

Next: [Write the USB with dd](08-write-the-usb.md).
