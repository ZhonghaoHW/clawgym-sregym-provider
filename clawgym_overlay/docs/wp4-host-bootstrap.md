# WP4 host bootstrap authorization

The ECS worker must not receive a password, cloud credential, broad sudo rule,
or a root-capable Git checkout. The administrator performs one controlled
installation of the reviewed bootstrap script; afterwards `ecs-user` can run
only that immutable root-owned file with `sudo -n`.

## Administrator procedure

From a local checkout at the approved provider commit, first record the exact
script digest:

```bash
shasum -a 256 clawgym_overlay/bootstrap/wp4-host-bootstrap.sh
```

Upload that exact file to a temporary `ecs-user` location using Workbench. In
an administrator-owned ECS terminal, compare the uploaded digest with the
recorded value, then run:

```bash
sudo install -d -o root -g root -m 0755 /usr/local/lib/clawgym
sudo install -o root -g root -m 0755 \
  /home/ecs-user/wp4-host-bootstrap.sh \
  /usr/local/lib/clawgym/wp4-host-bootstrap.sh
sudo install -o root -g root -m 0440 \
  /home/ecs-user/ecs-user-wp4.sudoers \
  /etc/sudoers.d/clawgym-wp4
sudo visudo -cf /etc/sudoers.d/clawgym-wp4
```

The first worker invocation is then:

```bash
sudo -n /usr/local/lib/clawgym/wp4-host-bootstrap.sh
```

It installs Docker from Docker's signed Ubuntu repository, prepares `/run/udev`
for the pinned Kind configuration, and persists only the two inotify settings
needed for the worker. It does not install Kind, kubectl, Helm, or uv; staging
will resolve and lock those user-local tools and all runtime assets before the
formal cluster is built.

After Docker group membership changes, start a new `ecs-user` login session
before running Docker. The later formal deployment lock will record exact
Docker packages, tool binaries, images, manifests, and charts; this bootstrap
is deliberately only the prerequisite stage and is never formal episode
evidence.

The separate `bootstrap/stage-user-tools.sh` runs as `ecs-user` and verifies
the fixed staging Kind, kubectl, Helm, and uv downloads before installing them
under `/home/ecs-user/.local/bin`. It has no sudo authority.

`bootstrap/staging-kind-config.yaml` is only the temporary four-node staging
cluster topology. The formal cluster uses `kind.wp4.formal.yaml`; its exact
file digest is bound by the execution profile alongside the published
deployment lock, so either topology or dependency changes create a new
`EnvironmentRelease`.

Docker 29's containerd image store exports multi-platform indexes even when
only the worker's platform content is present, which is incompatible with
Kind's all-platform archive import. The formal preloader therefore asks each
Kind node's containerd to pull the exact locked digest for `linux/amd64`, then
adds only the manifest's declared runtime tag inside that node. Mutable image
resolution remains forbidden.

## GitHub App source access

The worker obtains source only through the explicitly installed read-only
GitHub App. Its long-lived private key is an administrator-installed
`root:root` `0600` file at `/etc/clawgym/github-app.pem`; it is not part of a
checkout, a release, an artifact, or the `ecs-user` account. A root-owned token
helper signs a GitHub App JWT and caches the resulting installation token only
under `/run/clawgym` with `0600` permissions. GitHub installation tokens expire
after one hour; the cache is therefore transient and is removed on reboot.

Install the two reviewed helper scripts and updated sudoers file as root from
an approved provider commit:

```bash
install -o root -g root -m 0750 github-app-installation-token \
  /usr/local/lib/clawgym/github-app-installation-token
install -o root -g root -m 0750 github-app-git-credential \
  /usr/local/lib/clawgym/github-app-git-credential
install -o root -g root -m 0440 ecs-user-wp4.sudoers \
  /etc/sudoers.d/clawgym-wp4
visudo -cf /etc/sudoers.d/clawgym-wp4
```

`ecs-user` configures Git only once, with no credential store:

```bash
git config --global credential.useHttpPath true
git config --global credential.helper '!sudo -n /usr/local/lib/clawgym/github-app-git-credential'
```

The credential helper responds only for the two explicitly approved GitHub
HTTPS paths. It does not accept arguments that could select another app,
installation, key, host, or repository. The Git helper runs only through its
single exact sudoers entry; the installation-token command itself is not
sudo-authorized for `ecs-user`.
