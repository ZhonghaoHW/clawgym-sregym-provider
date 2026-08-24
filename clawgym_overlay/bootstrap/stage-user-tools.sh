#!/usr/bin/env bash
# Download WP4 staging tools as ecs-user, verifying each official SHA-256 file.
# This is staging only: exact resulting binary digests feed the later deployment
# lock and this script must not be used as formal episode evidence.

set -euo pipefail

wp4_bin_dir=/home/ecs-user/.local/bin
wp4_staging_parent=/home/ecs-user/.local/wp4-staging
wp4_stage_dir=$(mktemp -d "${wp4_staging_parent}/tools.XXXXXX")
trap 'rm -rf "$wp4_stage_dir"' EXIT

verify_pair() {
  local artifact=$1
  local checksum_file=$2
  local actual
  local expected
  actual=$(sha256sum "$artifact" | awk '{print $1}')
  expected=$(awk 'NR == 1 {print $1}' "$checksum_file")
  test "$actual" = "$expected"
}

cd "$wp4_stage_dir"

curl -fsSLO https://kind.sigs.k8s.io/dl/v0.31.0/kind-linux-amd64
curl -fsSLO https://kind.sigs.k8s.io/dl/v0.31.0/kind-linux-amd64.sha256sum
verify_pair kind-linux-amd64 kind-linux-amd64.sha256sum
install -m 0755 kind-linux-amd64 "$wp4_bin_dir/kind"

curl -fsSLO https://dl.k8s.io/release/v1.36.4/bin/linux/amd64/kubectl
curl -fsSLO https://dl.k8s.io/release/v1.36.4/bin/linux/amd64/kubectl.sha256
verify_pair kubectl kubectl.sha256
install -m 0755 kubectl "$wp4_bin_dir/kubectl"

curl -fsSLO https://get.helm.sh/helm-v4.2.0-linux-amd64.tar.gz
curl -fsSLO https://get.helm.sh/helm-v4.2.0-linux-amd64.tar.gz.sha256sum
verify_pair helm-v4.2.0-linux-amd64.tar.gz helm-v4.2.0-linux-amd64.tar.gz.sha256sum
tar -xzf helm-v4.2.0-linux-amd64.tar.gz
install -m 0755 linux-amd64/helm "$wp4_bin_dir/helm"

curl -fsSLO https://github.com/astral-sh/uv/releases/download/0.12.1/uv-x86_64-unknown-linux-gnu.tar.gz
curl -fsSLO https://github.com/astral-sh/uv/releases/download/0.12.1/uv-x86_64-unknown-linux-gnu.tar.gz.sha256
verify_pair uv-x86_64-unknown-linux-gnu.tar.gz uv-x86_64-unknown-linux-gnu.tar.gz.sha256
tar -xzf uv-x86_64-unknown-linux-gnu.tar.gz
install -m 0755 uv-x86_64-unknown-linux-gnu/uv "$wp4_bin_dir/uv"

sha256sum "$wp4_bin_dir/kind" "$wp4_bin_dir/kubectl" "$wp4_bin_dir/helm" "$wp4_bin_dir/uv" \
  > "${wp4_staging_parent}/tool-sha256s.txt"
