# 9. Install and connect

[The USB is ready](08-write-the-usb.md). The next destructive operation is on
the machine that boots it.

## Before booting

The preseed selects the first disk returned by the installer and confirms
partitioning automatically. Existing partitions, LVM, or RAID data can be erased.
There is no safe assumption that it will select your intended internal disk.

Back up anything needed, identify the installation target, and disconnect disks
you must preserve. Confirm that this is the fresh/reinstall machine, not the
live host you merely intended to update. Do not boot the unattended image
until that destructive installation is authorized.

It is a netinst image. Network access and the configured static IP, gateway,
and DNS need to work for package installation. Avoid giving a new/test machine
the same IP as live Sugar.

After installation, remove the USB and boot the installed disk. Otherwise
you risk returning to the unattended installer.

## A VM can help, but is not automatically isolated

The existing launcher is:

```sh
./generate-isos/run-preseed-iso.sh lab
```

Here `lab` means a separately configured test host whose ISO you built; it is
not an existing entry supplied by the repository. It needs libvirt/
`virt-install`. Its default bridge is `br0`; that can put the VM on your real
LAN. Build a separate test hostname with an unused IP instead of booting a
Sugar-IP image beside the live Sugar host.

The script creates a qcow2 disk on first run and reuses it later. Rerunning
an unattended installer against that disk can erase the prior VM installation.
It is not a fresh temporary test every time.

Memory, disk size, bridge, and OS variant can be selected with the documented
`VIRT_*` variables in `generate-isos/README.md`.

## Verify SSH, then Ansible

For the established Sugar example:

```sh
ssh -i ~/.ssh/id_ecdsa_sugar sugar@192.168.1.100
```

Confirm the new host's SSH fingerprint through a trusted source before accepting
it. A changed-host-key warning after reinstall can be expected, but do not
remove the old entry without confirming that this really is the replacement.

Only after that confirmation:

```sh
ssh-keygen -R 192.168.1.100
```

Reconnect and verify the new fingerprint. Ensure the inventory's username,
address, and private-key path match this installation, then:

```sh
ansible -i provision-hosts/inventory.yml sugar -m ping
```

The ISO installs Python/SSH and sets passwordless sudo for its user. A successful
Ansible ping gets you back to the existing-host workflow: provision intended
services, configure the app, and deploy.

Next: [Take over](10-take-over.md).
