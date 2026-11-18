#!/usr/bin/env python3
"""Check deploy/.env, or the process environment when started inside the image."""
import os
import sys
from pathlib import Path

from envfile import production_issues, read_env


def main():
    if len(sys.argv) > 1:
        source = read_env(Path(sys.argv[1]))
    else:
        source = os.environ
    errors, warnings = production_issues(source)
    for warning in warnings:
        print('Внимание: ' + warning, file=sys.stderr)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
