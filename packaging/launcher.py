"""PyInstaller entry point for the one-file `tessera` binary (appmap.mp T2).

Double-clicking the binary (no arguments, no TTY expectations broken) opens
the app; from a shell it is the full CLI.
"""
import sys

from tessera.cli import main

if __name__ == "__main__":
    argv = sys.argv[1:]
    sys.exit(main(argv if argv else ["app"]))
