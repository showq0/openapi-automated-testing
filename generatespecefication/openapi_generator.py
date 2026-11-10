"""Generate an OpenAPI YAML document from a source repository using Gemini."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs",
    ".cs", ".php", ".rb", ".scala", ".swift", ".ex", ".exs", ".proto",
    ".graphql", ".gql", ".yaml", ".yml", ".json", ".xml", ".md",
}
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "vendor", "dist", "build",
    "__pycache__", ".next", "coverage", "target",
}
MAX_SOURCE_CHARS = 180_000

SYSTEM_INSTRUCTIONS = """You are an API reverse-engineering assistant. Inspect the supplied repository files and produce a valid OpenAPI 3.0.3 YAML specification.

Determine the actual transport protocol, base URL and latest API version from repository evidence. Include only implemented, externally reachable API endpoints; do not invent routes. Trace handlers, request validation, authentication and response behavior to document path/query/header parameters, request bodies, response schemas, status codes and security schemes. Use reusable components where useful. If the source does not establish a value, use a clear placeholder only for server URL and explain uncertainty in the API description; never fabricate endpoint behavior.

Return only the YAML document, without Markdown fences or commentary. It must contain openapi, info, paths, and components as appropriate and parse as YAML."""


def collect_source(repository: Path, max_chars: int = MAX_SOURCE_CHARS) -> str:
    """Collect likely source and API configuration files without traversing dependencies."""
    blocks: list[str] = []
    used = 0
    files = sorted(
        path for path in repository.rglob("*")
        if path.is_file()
        and not any(part in SKIP_DIRS or part.startswith(".") for part in path.relative_to(repository).parts[:-1])
        and path.suffix.lower() in SOURCE_SUFFIXES
    )
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        content = content[:remaining]
        relative = path.relative_to(repository).as_posix()
        block = f"\n--- FILE: {relative} ---\n{content}\n"
        blocks.append(block)
        used += len(block)
    if not blocks:
        raise ValueError(f"No readable source or API definition files found in {repository}.")
    return "".join(blocks)


def _response_text(response: dict) -> str:
    """Extract model text from a Gemini Interactions API result."""
    chunks = [
        content.get("text", "")
        for step in response.get("steps", [])
        if step.get("type") == "model_output"
        for content in step.get("content", [])
        if content.get("type") == "text"
    ]
    text = "\n".join(chunk for chunk in chunks if chunk).strip()
    if not text:
        raise ValueError("Gemini returned no generated text.")
    return text


def generate_spec(source: str, api_key: str, model: str = DEFAULT_MODEL,
                  endpoint: str = DEFAULT_ENDPOINT) -> str:
    body = {
        "model": model,
        "input": "Analyze these source files and generate the OpenAPI specification:\n\n" + source,
        "system_instruction": SYSTEM_INSTRUCTIONS,
        "store": False,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as result:
            response = json.loads(result.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AI API request failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to AI API: {exc.reason}") from exc
    return _response_text(response).strip()


def validate_spec(document: str) -> dict:
    """Parse YAML and check the required OpenAPI document structure."""
    if document.startswith("```"):
        document = document.strip().removeprefix("```yaml").removeprefix("```yml").removeprefix("```")
        document = document.removesuffix("```").strip()
    try:
        spec = yaml.safe_load(document)
    except yaml.YAMLError as exc:
        raise ValueError(f"Generated output is not valid YAML: {exc}") from exc
    if not isinstance(spec, dict):
        raise ValueError("Generated YAML must contain an OpenAPI mapping.")
    if not str(spec.get("openapi", "")).startswith("3."):
        raise ValueError("Generated document must declare an OpenAPI 3.x version.")
    if not isinstance(spec.get("info"), dict) or not spec["info"].get("title") or not spec["info"].get("version"):
        raise ValueError("Generated document must include info.title and info.version.")
    if not isinstance(spec.get("paths"), dict):
        raise ValueError("Generated document must include a paths mapping.")
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path, help="Source repository to inspect")
    parser.add_argument("output", type=Path, help="Output OpenAPI YAML file")
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", DEFAULT_MODEL), help="Gemini model (or set GEMINI_MODEL)")
    parser.add_argument("--endpoint", default=os.getenv("GEMINI_INTERACTIONS_URL", DEFAULT_ENDPOINT), help="Gemini Interactions API URL")
    args = parser.parse_args(argv)
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Set GEMINI_API_KEY before running the generator.", file=sys.stderr)
        return 2
    if not args.repository.is_dir():
        print(f"Source repository directory not found: {args.repository}", file=sys.stderr)
        return 2
    try:
        source = collect_source(args.repository.resolve())
        spec_text = generate_spec(source, api_key, args.model, args.endpoint)
        spec = validate_spec(spec_text)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"OpenAPI specification written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
