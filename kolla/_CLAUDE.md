# CLAUDE.md

This file provides guidance to Claude Code when working with the Kolla codebase.

## Project Overview

Kolla is an OpenStack project that builds production-ready Docker/OCI container images for OpenStack services. It uses Jinja2-templated Dockerfiles with a powerful macro system to generate images for 61+ services.

**Key deliverables in the Kolla ecosystem:**
- **kolla** (this repo) - Container image building system
- **kolla-ansible** - Ansible playbooks for deployment
- **kayobe** - Additional deployment layer

## Directory Structure

```
kolla/
├── docker/                 # Dockerfile Jinja2 templates (61 services)
│   ├── base/               # Base image (foundation for all images)
│   ├── openstack-base/     # Common OpenStack foundation
│   ├── nova/               # Nova compute service (10 variants)
│   ├── neutron/            # Neutron networking (10+ variants)
│   ├── kerbside/           # Custom SPICE console images
│   ├── macros.j2           # Shared Jinja2 macros
│   └── ...
├── kolla/                  # Python package (build system)
│   ├── cmd/build.py        # CLI entry point: kolla-build
│   ├── image/              # Image building logic
│   │   ├── build.py        # Build orchestration
│   │   ├── kolla_worker.py # Main worker class
│   │   └── tasks.py        # Build/push task classes
│   ├── common/             # Configuration, sources, utilities
│   │   ├── config.py       # oslo.config options
│   │   └── sources.py      # Project source definitions
│   └── template/           # Jinja2 extensions
│       ├── methods.py      # Custom template methods
│       └── filters.py      # Custom template filters
├── etc/                    # Configuration examples
├── contrib/                # Template override samples
├── roles/                  # Ansible roles for CI
└── tests/                  # Functional tests
```

## Build Commands

### Basic Build
```bash
# Build all images
kolla-build

# Build specific image(s) by regex
kolla-build nova
kolla-build ^nova-api$
kolla-build "nova|neutron"

# Build for specific distro
kolla-build --base debian
kolla-build --base ubuntu
kolla-build --base rocky
```

### Build Options
```bash
# Specify registry and namespace
kolla-build --registry myregistry.io --namespace myproject

# Control parallelism
kolla-build --threads 16 --push-threads 4

# Push after building
kolla-build --push

# Use Podman instead of Docker
kolla-build --engine podman

# Specify OpenStack release
kolla-build --openstack-release 2025.1

# Apply custom template overrides
kolla-build --template-override /path/to/overrides.yaml
```

### Testing
```bash
# Run unit tests
tox -e py310-docker

# Code style checks
tox -e pep8

# Generate config file
tox -e genconfig
```

## Dockerfile Template System

### Image Inheritance Hierarchy
```
base (debian/ubuntu/centos/rocky)
  └─ openstack-base
       ├─ nova-base → nova-api, nova-compute, nova-conductor, ...
       ├─ neutron-base → neutron-server, neutron-openvswitch-agent, ...
       ├─ keystone-base → keystone, keystone-fernet
       ├─ kerbside-base → kerbside-api, kerbside-proxy
       └─ ...
```

### Template Structure
Templates use Jinja2 with block-based inheritance:

```jinja2
FROM {{ namespace }}/{{ image_prefix }}nova-base:{{ tag }}
LABEL maintainer="{{ maintainer }}" name="{{ image_name }}"

{% import "macros.j2" as macros with context %}

{% set nova_api_packages = ['package1', 'package2'] %}
{{ macros.install_packages(nova_api_packages | customizable("packages")) }}

COPY extend_start.sh /usr/local/bin/kolla_nova_extend_start
{{ macros.kolla_patch_sources() }}
```

### Key Macros (docker/macros.j2)
- `install_packages()` - Distro-aware package installation (apt/dnf)
- `install_pip()` - Python package installation with constraints
- `configure_user()` - Service user/group creation
- `kolla_patch_sources()` - Apply patches during build
- `enable_extra_repos()` / `disable_extra_repos()` - Repository management

### Template Variables
- `namespace` - Image registry namespace
- `image_prefix` - Prefix for image names
- `tag` - Image tag/version
- `base_distro` - debian/ubuntu/centos/rocky
- `base_arch` - x86_64/aarch64
- `base_package_type` - rpm/deb
- `distro_package_manager` - dnf/apt

### Customization Filter
The `customizable` filter allows per-image overrides:
```jinja2
{{ packages | customizable("packages") }}
```

Override in template-override YAML:
```yaml
nova_api_packages_append:
  - extra-package
nova_api_packages_remove:
  - unwanted-package
```

## Configuration

### Supported Distributions
- **Debian 12** (bookworm) - default
- **Ubuntu 24.04** LTS
- **Rocky Linux 10**
- **CentOS Stream 10**

### Architectures
- x86_64 (amd64)
- aarch64 (arm64)

### Build Profiles
Predefined image groups in config.py:
- **infra**: etcd, fluentd, haproxy, mariadb, memcached, rabbitmq, redis
- **main**: cinder, glance, heat, horizon, keystone, neutron, nova, placement
- **aux**: aodh, ironic, magnum, manila, octavia, trove, zun, kerbside

## Source Definitions

Project sources are defined in `kolla/common/sources.py`:
- OpenStack projects from tarballs.opendev.org
- Git repositories for specific projects
- SHA256 checksums for verification
- Version pinning per OpenStack release

## Key Files

| File | Purpose |
|------|---------|
| `docker/base/Dockerfile.j2` | Foundation image for all others |
| `docker/macros.j2` | Shared Jinja2 macros |
| `kolla/common/config.py` | All configuration options |
| `kolla/common/sources.py` | Project source URLs and versions |
| `kolla/image/kolla_worker.py` | Main build worker class |
| `kolla/template/filters.py` | Custom Jinja2 filters (customizable) |

## Adding a New Service Image

1. Create directory: `docker/myservice/`
2. Create base template: `docker/myservice/Dockerfile.j2`
3. Add source definition in `kolla/common/sources.py` if needed
4. Add user definition in `kolla/common/users.py` if needed
5. Create variant subdirectories if multiple images needed

### Minimal Dockerfile.j2 Template
```jinja2
FROM {{ namespace }}/{{ image_prefix }}openstack-base:{{ tag }}
LABEL maintainer="{{ maintainer }}" name="{{ image_name }}"

{% import "macros.j2" as macros with context %}

{% set myservice_packages = ['myservice-package'] %}
{{ macros.install_packages(myservice_packages | customizable("packages")) }}

{% set myservice_pip_packages = ['myservice'] %}
{{ macros.install_pip(myservice_pip_packages | customizable("pip_packages")) }}

COPY extend_start.sh /usr/local/bin/kolla_extend_start
RUN chmod 755 /usr/local/bin/kolla_extend_start

USER myservice
```

## Build Process Internals

1. **Template Discovery**: Scan docker/ for Dockerfile.j2 files
2. **Template Rendering**: Render Jinja2 to actual Dockerfiles
3. **Dependency Resolution**: Build parent-child image tree
4. **Parallel Build**: Execute builds with thread pool (default 8 threads)
5. **Push** (optional): Push to registry with separate thread pool

### File Time Handling
All file times are set to epoch (0) before building to ensure consistent Docker layer caching based on content hash rather than timestamps.

## Notes for Kerbside Integration

The `docker/kerbside/` directory contains custom SPICE console images:
- `kerbside-base/` - Base image with kerbside package
- `kerbside-api/` - API server
- `kerbside-proxy/` - SPICE proxy

These follow the standard Kolla template patterns and inherit from `openstack-base`.
