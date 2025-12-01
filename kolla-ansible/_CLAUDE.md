# Kolla Ansible - OpenStack Deployment Automation

This document provides context and guidelines for working with the Kolla
Ansible codebase, which is an OpenStack Foundation project for deploying
OpenStack clouds using containerized services orchestrated by Ansible.

## Project Overview

Kolla Ansible provides production-ready containers and deployment tools for
operating OpenStack clouds. It deploys OpenStack services and infrastructure
components in Docker/Podman containers using Ansible orchestration.

**Mission:** Enable operators (both inexperienced and experienced) to deploy
and manage OpenStack clouds quickly while allowing extensive customization.

## Architecture

### Directory Structure

- `ansible/` - Core Ansible playbooks, roles, and plugins
  - `site.yml` - Main orchestration playbook (1034 lines)
  - `roles/` - 80+ Ansible roles for OpenStack services and infrastructure
  - `group_vars/all/` - 60+ service-specific configuration files
  - `action_plugins/` - Config merge plugins
  - `filter_plugins/` - Custom Jinja2 filters
  - `library/` - Custom Ansible modules (kolla_container, etc.)
  - `module_utils/` - Container worker abstractions
- `kolla_ansible/` - Python package with CLI tools and utilities
  - `cmd/` - CLI commands (genpwd, mergepwd, readpwd, writepwd)
  - `cli/` - Cliff-based CLI interface
  - Core modules: ansible.py, utils.py, filters.py, etc.
- `etc/kolla/` - Configuration templates (globals.yml, passwords.yml)
- `tests/` - Unit and integration tests
- `tools/` - Deployment and validation utilities
- `doc/` - Sphinx documentation
- `deploy-guide/` - Deployment guides

### Component Categories

**OpenStack Services (50+ roles):**
- Compute: Nova, Ironic, Cyborg, Masakari
- Storage: Cinder, Manila, Glance, Trove
- Networking: Neutron, OVN, Kuryr, Octavia
- Identity: Keystone
- Orchestration: Heat, Mistral
- Monitoring: Aodh, Ceilometer, Prometheus, Grafana
- Logging: Opensearch, Fluentd
- Other: Designate, Tacker, Blazar, Magnum, Zun, Barbican, CloudKitty,
  Watcher, Skyline

**Infrastructure Components (20+ roles):**
- Databases: MariaDB, Memcached, Valkey
- Messaging: RabbitMQ
- HA/LB: HAProxy, Keepalived, HACluster
- Monitoring: Prometheus, Telegraf, InfluxDB, Grafana, Collectd
- Logging: Fluentd, Opensearch
- Other: Etcd, Certificates, OpenVSwitch, iSCSI

**Utility Roles:**
- `common` - Base configuration and common setup
- `prechecks` - Pre-deployment validation
- `destroy` - Service cleanup
- `service-*` - Generic service helpers
- `kerbside` - Custom ShakenfIst-specific role

## Code Style and Conventions

### Python Code

- **License:** Apache 2.0
- **Quotes:** Single quotes for strings, double quotes for docstrings
- **Line Length:** 80 characters maximum
- **Whitespace:** Trim trailing whitespace from all lines
- **Style:** Follow OpenStack conventions
- **Versioning:** Uses PBR (Python Build Reasonableness)
- **Type Hints:** Present in utility functions
- **Error Handling:** Custom exceptions in `exception.py`

### Ansible Code

- **Format:** YAML with proper indentation
- **Templates:** Jinja2 templates with .j2 extension
- **Tagging:** Use tags for selective execution
- **Modularity:** Role-based architecture
- **Variable Precedence:** defaults → group_vars → role vars
- **Documentation:** Include comments for complex logic

### Standard Role Structure

Each Ansible role typically contains:
```
role-name/
├── defaults/main.yml     # Default variables
├── tasks/
│   ├── main.yml         # Main task orchestration
│   ├── config.yml       # Configuration tasks
│   ├── deploy.yml       # Deployment tasks
│   ├── bootstrap.yml    # Bootstrap tasks
│   └── upgrade.yml      # Upgrade tasks
├── handlers/main.yml    # Handler definitions
├── templates/           # Jinja2 configuration templates
├── vars/main.yml        # Additional variables
└── files/              # Static files
```

## Configuration Management

### Configuration Files

- `etc/kolla/globals.yml` - Main configuration template (450+ options)
- `etc/kolla/passwords.yml` - Auto-generated secure credentials
- `ansible/group_vars/all/*.yml` - Service-specific configurations

### Configuration Patterns

- **INI-style configs:** Merged using oslo.config
- **YAML configs:** Merged using custom merge_yaml action plugin
- **Templated configs:** Jinja2 templates with variable substitution
- **Service definitions:** Dictionaries with enable flags
- **Override strategy:** Layer configurations from defaults to specific

## Container Management

### Multi-Engine Support

- **Docker:** Original container engine
- **Podman:** Alternative container engine
- **Abstraction Layer:** `kolla_container_worker.py` with engine-specific
  implementations

### Container Patterns

- Pull images from registries
- Volume mounting for persistent data
- Environment variable configuration
- Health checks and restart policies
- Multi-stage bootstrap for database initialization

### Custom Ansible Modules

- `kolla_container.py` - Container lifecycle management
  - Actions: create, start, stop, restart, remove
  - Handles volumes and networks
- `kolla_container_facts.py` - Container introspection
- `kolla_toolbox.py` - Utility container for running commands

## Key Features

1. **Multi-Container Engine:** Docker and Podman support
2. **Flexible Deployment:** All-in-One, multi-node, HA configurations
3. **Configuration Management:** Merge and override strategies
4. **Service Customization:** Enable/disable services per environment
5. **High Availability:** HAProxy, Keepalived, Galera clustering
6. **Security:** TLS/SSL, Vault integration, fernet key rotation
7. **Database Sharding:** Distributed database support
8. **Monitoring & Logging:** Prometheus, Grafana, Opensearch, Fluentd
9. **Upgrade Path:** Service upgrade and migration capabilities
10. **Custom Extensions:** Support for custom roles and playbooks

## CLI Commands

Main CLI via `kolla-ansible` command (Cliff framework):

- `gather-facts` - Ansible fact gathering
- `install-deps` - Install Galaxy dependencies
- `prechecks` - Pre-deployment validation
- `genconfig` - Generate configuration
- `validate-config` - Config validation
- `bootstrap-servers` - Server bootstrap
- `pull` - Pull container images
- `certificates` - Certificate generation
- `deploy` - Full deployment
- `reconfigure` - Reconfigure services
- `upgrade` - Service upgrades
- `stop` / `destroy` - Service management
- `prune-images` - Image cleanup
- `check` - Health checks
- Service-specific commands (mariadb-backup, rabbitmq-reset-state, etc.)

Password management utilities:
- `kolla-genpwd` - Generate passwords
- `kolla-mergepwd` - Merge password files
- `kolla-writepwd` - Write passwords
- `kolla-readpwd` - Read passwords

## Development Workflow

### Making Changes

1. **Understand Before Modifying:** Always read files before editing
2. **Follow Patterns:** Match existing code style and structure
3. **Test Changes:** Run prechecks and validation
4. **Avoid Over-Engineering:** Keep solutions simple and focused
5. **Security:** Check for OWASP top 10 vulnerabilities
6. **Documentation:** Update relevant docs and comments

### Adding New Services

1. Create role structure in `ansible/roles/`
2. Add service configuration to `ansible/group_vars/all/`
3. Define service in appropriate playbook
4. Add enable flag and default variables
5. Create templates for configuration files
6. Add handlers for service restarts
7. Include prechecks and validation
8. Update documentation

### Testing

- Unit tests in `tests/`
- Testinfra for infrastructure validation
- Zuul CI integration
- Use `tools/validate-*.py` scripts
- Run `tox` for local testing

## Dependencies

### Python Dependencies

- ansible-core >=2.18, <2.20
- oslo.config, oslo.utils - OpenStack utilities
- Jinja2 >= 3 - Templating
- cryptography, bcrypt, passlib - Security
- hvac - HashiCorp Vault
- cliff >= 4.7 - CLI framework
- jmespath - JSON query

### System Requirements

- Linux-based operating system
- Docker or Podman container engine
- Python 3.11+

## ShakenfIst Kerbside Integration

This codebase includes **Kerbside** - a ShakenfIst-specific integration:

- Custom Ansible role: `ansible/roles/kerbside/`
- Configuration: `ansible/group_vars/all/kerbside.yml`
- Ports:
  - API: 13002
  - Prometheus: 13003
  - VDI: 5898-5899
- Features:
  - TLS backend support
  - Keystone authentication integration
  - Container-based deployment

## Common Tasks

### Enabling a Service

Edit `etc/kolla/globals.yml` or inventory group_vars:
```yaml
enable_service_name: 'yes'
```

### Customizing Configuration

1. Edit service-specific YAML in `ansible/group_vars/all/`
2. Or override in inventory group_vars or host_vars
3. Or use custom config file merging

### Running a Deployment

```bash
kolla-ansible -i inventory prechecks
kolla-ansible -i inventory deploy
kolla-ansible -i inventory post-deploy
```

### Debugging

- Check container logs: `docker logs <container>`
- Verify configuration: `kolla-ansible validate-config`
- Run health checks: `kolla-ansible check`
- Use `tools/diag` for diagnostics

## Important Notes

- **Never commit without permission:** Always ask before committing changes
- **Read files first:** Always use Read tool before editing
- **Security-conscious:** Check for injection vulnerabilities
- **Minimal changes:** Only modify what's necessary
- **Follow precedent:** Match existing patterns and conventions
- **Test thoroughly:** Run prechecks and validation before deployment
- **Document changes:** Update relevant documentation files

## References

- Main playbook: [ansible/site.yml](ansible/site.yml)
- Container module: [ansible/library/kolla_container.py](ansible/library/kolla_container.py)
- CLI entry point: [kolla_ansible/cmd/kolla_ansible.py](kolla_ansible/cmd/kolla_ansible.py)
- Configuration template: [etc/kolla/globals.yml](etc/kolla/globals.yml)
- Kerbside role: [ansible/roles/kerbside/](ansible/roles/kerbside/)

## License

Apache License 2.0 - OpenStack Foundation project
