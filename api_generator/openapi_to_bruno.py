"""Import OpenAPI into Bruno and append schema-derived request scenarios."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from bruno_converter import convert


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Path to an OpenAPI YAML or JSON specification")
    parser.add_argument("output", type=Path, help="Directory where Bruno creates the collection")
    parser.add_argument("--name", help="Bruno collection name (defaults to the source filename)")
    args = parser.parse_args(argv)

    try:
        convert(args.source, args.output, args.name)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"Bruno import failed with exit code {exc.returncode}.", file=sys.stderr)
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
