# OpenStack services from master, for import-memory scans. Built by scan.sh,
# which puts upper-constraints.txt in the build context. The probe scripts are
# not copied in: scan.sh mounts this directory, so editing roles.txt needs no
# rebuild.
FROM python:3.12-slim-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ pkg-config libvirt-dev libffi-dev libssl-dev libpq-dev \
        git procps && \
    rm -rf /var/lib/apt/lists/*

# Space separated pip requirements; override with --build-arg to scan other
# projects or to pin a branch (git+https://opendev.org/openstack/nova@stable/2026.1).
ARG PROJECTS="git+https://opendev.org/openstack/nova git+https://opendev.org/openstack/neutron git+https://opendev.org/openstack/cinder git+https://opendev.org/openstack/glance git+https://opendev.org/openstack/keystone git+https://opendev.org/openstack/placement"
ARG EXTRAS="libvirt-python PyMySQL python-memcached"

COPY upper-constraints.txt /uc.txt
# The services themselves are listed in upper-constraints and would conflict
# with the git installs.
RUN sed -i '/^\(nova\|neutron\|cinder\|glance\|keystone\|openstack-placement\)===/d' /uc.txt && \
    pip install --no-cache-dir -c /uc.txt ${PROJECTS} ${EXTRAS}
