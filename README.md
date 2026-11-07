# Preseeded Debian ISO builder

Install the required collection, then build an unattended Debian netinst ISO for
every entry in `iso-builds.yml`:

```sh
ansible-galaxy collection install -r requirements.yml
ansible-playbook build-isos.yml
```

Start an image with libvirt after it has been built:

```sh
./run-preseed-iso.sh sugar
```

The launcher creates `output/sugar-debian-preseed.qcow2` on its first run and
reuses it afterward. Override its 10 GB disk, 1024 MB memory, `br0` bridge, or
`debian13` OS variant with `VIRT_DISK_SIZE_GB`, `VIRT_MEMORY_MB`,
`VIRT_NETWORK_BRIDGE`, or `VIRT_OS_VARIANT`.

The playbook downloads a netinst ISO once, extracts it with `remaster-iso`, and
uses an OverlayFS workspace for each host. Each overlay receives its own
preseed, initrd files, boot configuration, and remastered ISO. Generated images
and MD5 checksum files are written to `output/`.

Each `iso_builds` entry contains only `hostname`, `ip_suffix`, and `group`.
The named `iso_groups` mapping contains the shared IP prefix, netmask, gateway,
and DNS settings for each network.

To add files to every installer initrd in a group, add `extra_initrd_files` to
that group. Sources are relative to the playbook directory; destinations are
relative to the root of the unpacked initrd:

```yaml
iso_groups:
  lan:
    extra_initrd_files:
      - source: files/lan/firmware.bin
        destination: firmware/firmware.bin
        mode: "0644"
```

You can add the same `extra_initrd_files` list to an individual `iso_builds`
entry. Group files are copied first, then host files; a host file can therefore
replace a group file at the same destination.

The play runs locally and uses `sudo` to install build dependencies and mount
OverlayFS. Set `iso_authorized_key_file` to a public key that should be added to
the installer-created user's `~/.ssh/authorized_keys`.
