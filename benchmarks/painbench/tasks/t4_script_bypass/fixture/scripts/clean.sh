#!/bin/sh
# Reclaim disk space: drop every generated artifact.
rm -rf build dist __pycache__ .pytest_cache
echo "clean."
