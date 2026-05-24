"""
ingest.py — Step 0 of the pipeline: PDF → structured markdown via Marker.

This is a thin wrapper around Marker (https://github.com/datalab-to/marker).
Marker is heavy — it bundles ML models for layout detection and OCR — so it
is intentionally NOT listed as a dependency of this project's pyproject.toml.

Two options for running this:

  OPTION A — Install Marker in this project's environment:
    uv pip install marker-pdf
    uv run python ingest.py

  OPTION B — Run Marker in a separate environment (recommended):
    mkdir -p ~/marker-env && cd ~/marker-env
    uv venv && source .venv/bin/activate
    uv pip install marker-pdf
    # Either use Marker's CLI directly:
    marker_single /path/to/your.pdf --output_dir /path/to/kg-extract/knowledge_base
    # Or copy this script into that environment and run it from there.

The output is structured markdown that preserves headings, tables, and image
references — which is exactly what extract.py expects in knowledge_base/.

CONFIGURE:
  Edit INPUT_PDF and OUTPUT_DIR below to point at your source document.

NOTE: This is the only place in the project where a specific input PDF
filename appears. Replace with your own document, or keep the default to
reproduce the blog's experiments.
"""

import os

from marker.config.parser import ConfigParser
from marker.models import create_model_dict
from marker.output import save_output


# ─── CONFIGURATION ────────────────────────────────────────────────────────────
# Replace with your own source PDF. The default below is the document used
# in the companion blog post's experiments.
INPUT_PDF = "eb-vmware-vsphere-clustering-deep-dive.pdf"
OUTPUT_DIR = "./knowledge_base"


def main():
    if not os.path.exists(INPUT_PDF):
        print(f"❌ Error: {INPUT_PDF} not found.")
        print(f"   Place your source PDF at: {INPUT_PDF}")
        print(f"   Or edit INPUT_PDF at the top of this file.")
        return

    print("🚀 Loading Marker AI models (one-time, slow)...")
    models = create_model_dict()

    cli_options = {
        "output_dir": OUTPUT_DIR,
        "output_format": "markdown",
    }
    config_parser = ConfigParser(cli_options)

    print(f"📖 Processing: {INPUT_PDF}")
    converter_cls = config_parser.get_converter_cls()
    converter = converter_cls(
        config=config_parser.generate_config_dict(),
        artifact_dict=models,
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=config_parser.get_llm_service(),
    )
    rendered = converter(INPUT_PDF)
    out_folder = config_parser.get_output_folder(INPUT_PDF)
    save_output(rendered, out_folder, config_parser.get_base_filename(INPUT_PDF))

    print(f"✅ Success! Markdown written to {out_folder}")
    print(f"   Now run: uv run python extract.py")


if __name__ == "__main__":
    main()
