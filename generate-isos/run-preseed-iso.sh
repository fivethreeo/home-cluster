#!/usr/bin/env bash
set -euo pipefail

hostname="${1:-}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${script_dir}/output"
iso_path="${output_dir}/${hostname}-debian-preseed.iso"
disk_path="${output_dir}/${hostname}-debian-preseed.qcow2"

if [[ -z "${hostname}" ]]; then
  echo "Usage: $0 <hostname>" >&2
  echo "Example: $0 sugar" >&2
  exit 1
fi

if [[ ! -f "${iso_path}" ]]; then
  echo "ISO not found: ${iso_path}" >&2
  exit 1
fi

if ! command -v virt-install >/dev/null 2>&1; then
  echo "virt-install is required. Install the virtinst package first." >&2
  exit 1
fi

disk_args=(--disk "path=${disk_path},format=qcow2")
if [[ ! -e "${disk_path}" ]]; then
  disk_args=(--disk "path=${disk_path},size=${VIRT_DISK_SIZE_GB:-10},format=qcow2")
fi

virt-install \
  --name="${hostname}-debian-preseed" \
  --memory="${VIRT_MEMORY_MB:-1024}" \
  "${disk_args[@]}" \
  --cdrom="${iso_path}" \
  --network "bridge=${VIRT_NETWORK_BRIDGE:-br0},model=virtio" \
  --os-variant="${VIRT_OS_VARIANT:-debian13}" \
  --graphics spice \
  --console pty,target_type=serial
