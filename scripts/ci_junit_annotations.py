"""Emit GitHub Actions ::error:: annotations from a pytest JUnit XML report."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    xml_path = Path(sys.argv[1] if len(sys.argv) > 1 else "pytest-core.xml")
    if not xml_path.is_file():
        print(f"::error title=PytestJUnitMissing::{xml_path} was not created (pytest crashed or exited early)")
        return 0
    tree = ET.parse(xml_path)
    count = 0
    for tc in tree.iter("testcase"):
        for ch in tc:
            if ch.tag in ("failure", "error"):
                count += 1
                title = f"{tc.attrib.get('classname', '')}.{tc.attrib.get('name', '')}"
                body = f"{ch.attrib.get('message', '')} | {ch.text or ''}"
                clean = " ".join(body.splitlines())[:900]
                print(f"::error title={title}::{clean}")
    if count == 0:
        suite = tree.getroot()
        print(
            f"::error title=PytestNonZeroNoTestFailure::Non-zero exit without testcase failure (coverage or collection): {suite.attrib}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
