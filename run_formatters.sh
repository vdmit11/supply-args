#!/usr/bin/env sh

set -e
set -x

isort supply_args tests
black supply_args tests
