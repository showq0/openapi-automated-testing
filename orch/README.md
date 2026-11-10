# Unified API test generation pipeline

`run_pipeline.py` connects the repository's existing Gemini source scanner and OpenAPI-to-Bruno converter. It writes an OpenAPI document, imports the documented HTTP operations into Bruno, and checks that each documented method and path appears in the collection.

## Prerequisites

- Python 3.10 or newer
- PyYAML (the repository's root `requirements.txt`)
- A Gemini API key
- Bruno CLI installed with `bru` available on `PATH`

From the repository root, prepare an isolated environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Set the key in your shell. Do not put it in source files:

```bash
export GEMINI_API_KEY="your-api-key"


```

## Run

From the repository root:

```bash
python orch/run_pipeline.py \
  --repo-path /path/to/source-repository \
  --output-dir ./generated-output
```
python orch/run_pipeline.py \
  --repo-path /Users/showqbalawneh/Documents/boot.dev/final-project\
  --output-dir ./generated_output
```

The output directory contains `openapi.yaml` and `bruno/`, the Bruno collection. Use `--collection-name "My API"` to set the collection's display name. The Gemini model and endpoint can also be configured with `GEMINI_MODEL` and `GEMINI_INTERACTIONS_URL`, as supported by the underlying specification generator.

## Validation and limitations

The pipeline checks that the generated OpenAPI YAML is readable, parses as YAML, declares OpenAPI 3.x, and contains HTTP operations. After conversion it checks that every documented method/path has a corresponding Bruno request. A failure stops the pipeline and names the stage, error, and command when applicable.

The existing source scanner sends selected repository files to Gemini and caps input at 180,000 characters. Gemini may miss endpoints or infer details incorrectly. Its current command-line interface does not expose a separate inventory of source-discovered endpoints, so this pipeline can validate documented-to-collection coverage but cannot independently compare source-discovered endpoints against the specification. Review the generated OpenAPI document against the source before relying on the collection. The existing Bruno scenario generator derives payload variants for operations with request-body schemas; bodyless operations still receive requests from the Bruno OpenAPI importer, but do not receive schema-derived positive and negative payload variants.

## Troubleshooting

- **`GEMINI_API_KEY is not set`**: export the key in the same shell used to run the pipeline.
- **PyYAML missing**: activate the virtual environment and run `python -m pip install -r requirements.txt`.
- **Bruno CLI not found**: install Bruno CLI and ensure `bru` is on `PATH`.
- **Coverage validation fails**: inspect the generated OpenAPI paths and collection requests; resolve the missing import or route before treating the run as successful.
- **Gemini/API errors**: check network access, key/model access, and `GEMINI_MODEL` or `GEMINI_INTERACTIONS_URL` if customized.
