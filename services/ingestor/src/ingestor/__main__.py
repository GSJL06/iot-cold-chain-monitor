"""Punto de ejecución como módulo: python -m ingestor."""

import sys

from ingestor.app import main

if __name__ == "__main__":
    sys.exit(main())
