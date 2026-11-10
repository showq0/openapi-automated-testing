# Generate an OpenAPI specification

This tool scans a source repository for API handlers, schemas and configuration, asks Google Gemini to derive the API contract through the Gemini Interactions API, validates the returned YAML structure, and writes an OpenAPI 3.x document.

## Requirements

- Python 3.10 or newer
- PyYAML (`python3 -m pip install -r requirements.txt` from the project root)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey) with access to the selected model

## Usage

```sh
export GEMINI_API_KEY="your-api-key"
python3 generatespecefication/openapi_generator.py /path/to/source-repository openapi/open-api.yml
```
example 
```sh
python3 generatespecefication/openapi_generator.py /Users/showqbalawneh/Documents/boot.dev/final-project openapi/open-api2.yml
```

The model defaults to `gemini-3.8-flash`. Override it with `--model` or `GEMINI_MODEL`. To change the Gemini Interactions API URL, set `--endpoint` or `GEMINI_INTERACTIONS_URL`.

The scanner includes common source, API-definition, configuration and Markdown files. It skips hidden directories and common dependency/build directories, and caps the submitted source at 180,000 characters. Review the generated specification against the source before using it: the model can miss routes or infer details incorrectly, and the structural validation does not replace a full OpenAPI validator.
