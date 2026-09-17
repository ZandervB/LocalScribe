"""Render a locally supplied PDF into private PNG inputs for OCR evaluation.

The input and output folders are deliberately ignored by Git. This utility makes
no network calls and never uploads document content.
"""
import argparse
from pathlib import Path

import fitz


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()
    if args.dpi < 72 or args.dpi > 300:
        parser.error("--dpi must be between 72 and 300")
    document = fitz.open(args.pdf)
    args.output.mkdir(parents=True, exist_ok=True)
    scale = args.dpi / 72
    matrix = fitz.Matrix(scale, scale)
    print(f"Rendering {document.page_count} page(s) at {args.dpi} DPI")
    for number, page in enumerate(document, start=1):
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        path = args.output / f"page-{number:03}.png"
        pixmap.save(path)
        print(path)


if __name__ == "__main__":
    main()
