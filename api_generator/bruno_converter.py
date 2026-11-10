from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from openapi_parser import _yaml_module, load_spec
from bruno_writer import generate_collection_scenarios


def convert(source: Path, output: Path, collection_name: str | None = None) -> None:
    """Import into a clean staging directory, then merge generated files into output."""
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"OpenAPI source file does not exist: {source}")
    _yaml_module()  # Fail early with an actionable dependency message.
    load_spec(source)  # Validate the document before Bruno writes the collection.
    executable = shutil.which("bru")
    if executable is None:
        raise RuntimeError("Bruno CLI was not found; install it and ensure `bru` is on PATH.")
    name = collection_name or source.stem.replace("_", " ").replace("-", " ").title()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bruno-import-", dir=output.parent) as staging_dir:
        staging = Path(staging_dir) / "collection"
        subprocess.run(
            [
                executable, "import", "openapi",
                "--source", str(source),
                "--output", str(staging),
                "--collection-name", name,
            ],
            check=True,
        )
        count = generate_collection_scenarios(source, staging)
        _merge_generated_files(staging, output)
    print(f"Generated {count} schema-based Bruno request scenarios in {output}.")


def _merge_generated_files(source: Path, destination: Path) -> None:
    """Copy staged files without removing unrelated files already in destination."""
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            if target.exists() and not target.is_dir():
                target.unlink()
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
