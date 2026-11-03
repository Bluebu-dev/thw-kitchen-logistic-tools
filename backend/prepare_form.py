"""Create a draft field map; manually review it before registering a new original."""

import argparse
import json
from pathlib import Path
from .pdf_forms import extract_layout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--code", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    layout = extract_layout(args.pdf.read_bytes(), args.code)
    output = args.output or args.pdf.with_suffix(".json")
    with output.open("x") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)
    print(output)


if __name__ == "__main__":
    main()
