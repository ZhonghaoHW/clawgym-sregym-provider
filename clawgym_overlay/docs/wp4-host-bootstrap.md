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
