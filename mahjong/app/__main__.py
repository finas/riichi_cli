"""Run the headless JSON-lines game service."""

import sys

from .session import JsonLinesGameService


def main() -> None:
    JsonLinesGameService(sys.stdin, sys.stdout).run()


if __name__ == "__main__":
    main()
