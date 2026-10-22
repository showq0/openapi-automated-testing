from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from openapi_parser import _yaml_module, load_spec
from bruno_writer import generate_collection_scenarios


def convert(source: Path, output: Path, collection_name: str | None = None) -> None:
    """Use Bruno's importer, then append generated request scenarios."""
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
    subprocess.run(
        [
            executable, "import", "openapi",
            "--source", str(source),
            "--output", str(output),
            "--collection-name", name,
        ],
        check=True,
    )
    count = generate_collection_scenarios(source, output)
    print(f"Generated {count} schema-based Bruno request scenarios in {output}.")
