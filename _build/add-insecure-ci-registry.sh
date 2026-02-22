#!/bin/bash -e

# The CI registry is now served over HTTPS at 192.168.1.15:5050,
# so no insecure-registry Docker configuration is needed. This
# script is kept as a no-op for compatibility with callers.

echo "CI registry is secure (192.168.1.15:5050), no Docker config needed."
