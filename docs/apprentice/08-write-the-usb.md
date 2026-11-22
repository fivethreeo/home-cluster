# 8. Write the USB with dd

You have [built the intended ISO](07-build-an-iso.md). Now put it onto a USB
device that you are prepared to erase.

There are two independent destructive steps in this workflow:

1. `dd` overwrites the selected USB device on the controller.
2. Booting the unattended installer later erases its first detected disk.

This chapter addresses the first. Neither is safe to do by guessing a device
name.

## Identify the device before writing

Unplug the candidate USB and inspect disks, then plug it in and inspect again:

```sh
lsblk -dp -o NAME,SIZE,MODEL,SERIAL,TRAN
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS
```

Match the newly appeared disk using size, model/serial, and transport. Inspect
the whole disk and its mounted partitions. Do not assume `/dev/sdb` means USB;
names can change between boots or insertions.

The target must not be the disk containing `/`, `/home`, or data you need.
If it is ambiguous, stop and physically identify the device. A useful prompt is:

> help identify the usb, don't write yet

The AI can help interpret `lsblk`, but it should not choose an unverified disk
or write merely because a USB-looking name exists.

## Check the source and unmount the USB partitions

For the Sugar output:

```sh
cd generate-isos/output
md5sum -c sugar-debian-preseed.iso.md5sum
cd ../..
```

Unmount each mounted partition belonging to the confirmed USB. Substitute
the actual partition in this example:

```sh
sudo umount /dev/CONFIRMED_USB_PARTITION
```

Do not unmount arbitrary host filesystems or use a wildcard guessing all
partitions. Do not run the write while the USB filesystems remain mounted.

## Write to the whole device

**The command below erases the selected USB. Replace the placeholder only after
positively identifying the disposable whole disk and confirming the write.**

```sh
sudo dd if=generate-isos/output/sugar-debian-preseed.iso \
  of=/dev/CONFIRMED_USB_DISK bs=4M status=progress conv=fsync,nocreat
sync
```

`if` is the ISO input. `of` is the output **whole disk**, not a partition such
as `sdb1`. `bs=4M` selects a transfer block size. `status=progress` shows copying.
`fsync` flushes output before completion; `nocreat` prevents creating a regular
file when a nonexistent placeholder/path is supplied.

`nocreat` cannot protect a wrong existing disk. Neither can `sync`. Target
identification is the safety check.

Wait for `dd` to exit successfully and synchronization to finish before removing
the device. A truncated or failed copy is not installation media.

Next: [Install and connect](09-install-and-connect.md).
