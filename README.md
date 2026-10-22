# API tests

## Convert an OpenAPI specification to Bruno

### Prerequisites

- Install the [Bruno CLI](https://docs.usebruno.com/bru-cli/installation).
- Ensure the `bru` executable is available on your system's `PATH`.
- Install Python 3.

### Setup

Create and activate a Python virtual environment, then install the required dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
```


Run the converter with an OpenAPI YAML or JSON file and an output directory:
recomment to create openApi folder contains openapi file as input.


```sh
python3 scripts/openapi_to_bruno.py openapi/open-api.yml bruno/log-ingestion
```

The output directory is a Bruno collection directory, containing its collection configuration and request files, and can be opened in Bruno. After Bruno imports the OpenAPI document, the script resolves local schema references and adds request YAML files for a valid sample and constraint-based invalid payload scenarios. Each generated request includes a status assertion based on the declared OpenAPI responses. To choose the displayed collection name, pass `--name`:

```sh
python3 scripts/openapi_to_bruno.py openapi/open-api.yml bruno/log-ingestion --name "Log Ingestion API"
```

Run the converter's automated tests with:

```sh
python3 -m unittest discover -s tests
```

### Scenario extraction flow

For each operation with a request body, the script resolves local `$ref` pointers, walks the schema keywords (`required`, enums, formats, bounds, object properties, array items, and additional properties), and builds a representative valid payload. It then creates bounded invalid variants by removing required properties or violating one constraint at a time, and writes each case as a Bruno request with an expected status assertion.


 

