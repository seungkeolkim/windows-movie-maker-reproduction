"""Command-line entry point for the desktop application."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from movie_maker.application import run


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Movie Maker Reproduction")
    parser.add_argument(
        "--check",
        action="store_true",
        help="GUI 런타임을 초기화하고 버전 정보를 출력한 뒤 종료합니다.",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    options = parse_arguments(arguments)
    return run(check_only=options.check)


if __name__ == "__main__":
    raise SystemExit(main())
