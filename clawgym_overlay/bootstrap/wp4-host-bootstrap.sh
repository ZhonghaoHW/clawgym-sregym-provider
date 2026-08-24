#!/usr/bin/env bash
# Root-owned, one-purpose WP4 staging bootstrap for Ubuntu 24.04 execution hosts.
#
# This script is intentionally not run from a Git checkout. An administrator must
# verify its committed SHA-256, install it under /usr/local/lib/clawgym/, and make
# that installed copy root-owned before granting ecs-user NOPASSWD access to it.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  printf '%s\n' 'wp4_bootstrap_error=root_required' >&2
  exit 1
fi

if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  printf '%s\n' 'wp4_bootstrap_error=unsupported_platform' >&2
  exit 1
fi

if [ ! -r /etc/os-release ]; then
  printf '%s\n' 'wp4_bootstrap_error=os_release_missing' >&2
  exit 1
fi

. /etc/os-release
if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
  printf '%s\n' 'wp4_bootstrap_error=unsupported_os' >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install --yes --no-install-recommends ca-certificates curl gnupg uidmap

install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor --yes \
  --output /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

arch="$(dpkg --print-architecture)"
printf '%s\n' \
  "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install --yes --no-install-recommends \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable --now docker
usermod -aG docker ecs-user
install -d -m 0755 /run/udev
install -d -m 0755 /etc/sysctl.d
cat > /etc/sysctl.d/99-clawgym-wp4-inotify.conf <<'EOF'
fs.inotify.max_user_instances=65536
fs.inotify.max_user_watches=1048576
EOF
sysctl --system >/dev/null

printf 'wp4_bootstrap_docker_version='
docker --version | tr ' ' '_'
printf 'wp4_bootstrap_inotify_instances='
cat /proc/sys/fs/inotify/max_user_instances
printf 'wp4_bootstrap_inotify_watches='
cat /proc/sys/fs/inotify/max_user_watches
printf '%s\n' 'wp4_bootstrap_status=complete'
