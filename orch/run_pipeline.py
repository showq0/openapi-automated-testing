#!/usr/bin/env python3
"""Generate an OpenAPI specification and Bruno collection from an API repository."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None


ROOT = Path(__file__).resolve().parent.parent
SPEC_GENERATOR = ROOT / "generatespecefication" / "openapi_generator.py"
BRUNO_GENERATOR = ROOT / "api_generator" / "openapi_to_bruno.py"
METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


class PipelineError(RuntimeError):
    def __init__(self, stage: str, message: str, command: list[str] | None = None):
        super().__init__(message)
        self.stage = stage
        self.command = command


def endpoint_operations(spec: dict[str, Any]) -> set[tuple[str, str]]:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI document must contain a paths mapping.")
    found = {
        (method.upper(), path)
        for path, item in paths.items()
        if isinstance(item, dict)
        for method in item
        if method.lower() in METHODS
    }
    if not found:
        raise ValueError("OpenAPI document contains no HTTP operations.")
    return found


def _canonical_path(path: str) -> str:
    # Bruno and OpenAPI use different parameter spellings in some CLI versions.
    path = re.sub(r"^https?://[^/]+", "", path)
    path = re.sub(r"^\{\{[^}]+\}\}", "", path) or "/"
    return re.sub(r"\{[^{}]+\}|:[^/]+", "{}", path.rstrip("/") or "/")


def collection_operations(collection: Path) -> set[tuple[str, str]]:
    """Read HTTP operation declarations from Bruno's .bru and generated YAML files."""
    found: set[tuple[str, str]] = set()
    for file in collection.rglob("*"):
        if not file.is_file():
            continue
        if file.suffix.lower() in {".yml", ".yaml"} and yaml is not None:
            try:
                doc = yaml.safe_load(file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError):
                continue
            request = doc.get("http") if isinstance(doc, dict) else None
            if isinstance(request, dict) and isinstance(request.get("method"), str) and isinstance(request.get("url"), str):
                method = request["method"].upper()
                path = request["url"].split("?", 1)[0]
                path = re.sub(r"^https?://[^/]+", "", path)
                path = re.sub(r"^\{\{[^}]+\}\}", "", path) or "/"
                found.add((method, _canonical_path(path)))
        elif file.suffix.lower() == ".bru":
            try:
                content = file.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            for method, path in re.findall(r"(?im)^\s*(get|post|put|patch|delete|options|head|trace)\s+([^\s]+)", content):
                found.add((method.upper(), _canonical_path(path.split("?", 1)[0])))
    return found


def validate_generated_yaml_requests(collection: Path) -> None:
    """Check our schema-derived request files have Bruno HTTP and test sections."""
    for file in collection.rglob("*.yml"):
        try:
            doc = yaml.safe_load(file.read_text(encoding="utf-8")) if yaml is not None else None
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if not isinstance(doc, dict) or "http" not in doc:
            continue
        http = doc.get("http")
        scripts = doc.get("runtime", {}).get("scripts", []) if isinstance(doc.get("runtime"), dict) else []
        has_tests = isinstance(scripts, list) and any(
            isinstance(script, dict) and script.get("type") == "tests" and isinstance(script.get("code"), str) and "test(" in script["code"]
            for script in scripts
        )
        if not isinstance(http, dict) or not isinstance(http.get("method"), str) or not isinstance(http.get("url"), str) or not has_tests:
            raise PipelineError("Bruno validation", f"Generated request/test structure is invalid: {file}")


def run_step(stage: str, command: list[str], cwd: Path) -> None:
    try:
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        raise PipelineError(stage, str(exc), command) from exc
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"Process exited with code {result.returncode}."
        raise PipelineError(stage, detail, command)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")


def run(args: argparse.Namespace) -> None:
    if yaml is None:
        raise PipelineError("Environment setup", "PyYAML is missing. Install dependencies with `python -m pip install -r requirements.txt`.")
    if not args.repo_path.is_dir():
        raise PipelineError("Input validation", f"Source repository directory does not exist: {args.repo_path}")
    if not os.environ.get("GEMINI_API_KEY"):
        raise PipelineError("Environment setup", "GEMINI_API_KEY is not set. Export it before running the pipeline.")
    if not SPEC_GENERATOR.is_file() or not BRUNO_GENERATOR.is_file():
        raise PipelineError("Repository setup", "Could not find both generator entry points in this checkout.")

    out = args.output_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    spec_path = out / "openapi.yaml"
    collection = out / "bruno"
    print(f"[1/4] Generating OpenAPI specification from {args.repo_path.resolve()}")
    command = [sys.executable, str(SPEC_GENERATOR), str(args.repo_path.expanduser().resolve()), str(spec_path)]
    run_step("OpenAPI generation", command, ROOT)
    if not spec_path.is_file() or not os.access(spec_path, os.R_OK):
        raise PipelineError("OpenAPI validation", f"Generator completed but readable output is missing: {spec_path}", command)

    print("[2/4] Validating generated OpenAPI YAML")
    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        if not isinstance(spec, dict) or not str(spec.get("openapi", "")).startswith("3."):
            raise ValueError("Document must be an OpenAPI 3.x mapping.")
        documented = endpoint_operations(spec)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        raise PipelineError("OpenAPI validation", str(exc)) from exc

    print(f"[3/4] Importing {len(documented)} documented operations into Bruno")
    command = [sys.executable, str(BRUNO_GENERATOR), str(spec_path), str(collection), "--name", args.collection_name]
    run_step("Bruno generation", command, BRUNO_GENERATOR.parent)
    if not collection.is_dir():
        raise PipelineError("Bruno validation", f"Converter completed but collection directory is missing: {collection}", command)
    validate_generated_yaml_requests(collection)
    covered = collection_operations(collection)
    missing = sorted((method, path) for method, path in documented if (method, _canonical_path(path)) not in covered)
    if missing:
        details = ", ".join(f"{method} {path}" for method, path in missing)
        raise PipelineError("Bruno validation", f"Collection has no matching request for {len(missing)} documented operation(s): {details}", command)

    print(f"[4/4] Validated {len(documented)} documented operations against Bruno request files")
    print(f"OpenAPI: {spec_path}\nBruno collection: {collection}")
    print("Source endpoint discovery is performed by the existing Gemini generator; its CLI does not expose an independent discovered-endpoint inventory.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-path", required=True, type=Path, help="API source repository to inspect")
    parser.add_argument("--output-dir", type=Path, default=Path("generated-output"), help="Directory for openapi.yaml and Bruno collection")
    parser.add_argument("--collection-name", default="Generated API Tests", help="Name shown for the Bruno collection")
    args = parser.parse_args(argv)
    try:
        run(args)
    except PipelineError as exc:
        print(f"Pipeline failed during {exc.stage}: {exc}", file=sys.stderr)
        if exc.command:
            print("Command: " + " ".join(exc.command), file=sys.stderr)
        print("Suggested resolution: review the error above, verify the input and required dependencies, then rerun the failed stage.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
