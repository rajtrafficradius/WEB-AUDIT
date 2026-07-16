#!/usr/bin/env python
"""Django administrative entry point."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - exercised before dependencies exist
        raise ImportError(
            "Django is not installed. Install the project dependencies before running manage.py."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
