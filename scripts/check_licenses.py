#!/usr/bin/env python3
"""Fail CI when installed distributions declare a non-permissive license."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib.metadata import distributions
from pathlib import Path


APPROVED = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "ISCL",
    "MIT",
    "MIT-0",
    "MIT-CMU",
    "PSF-2.0",
    "Python-2.0",
    "CNRI-Python",
    "Unlicense",
    "Zlib",
}
ALIASES = {
    "Apache Software License": "Apache-2.0",
    "Apache License 2.0": "Apache-2.0",
    "BSD License": "BSD-3-Clause",
    "MIT License": "MIT",
    "Python Software Foundation License": "PSF-2.0",
    "ISC License (ISCL)": "ISC",
    "The Unlicense (Unlicense)": "Unlicense",
}
IGNORED_WORDS = {"AND", "OR", "License", "Version", "Software", "Foundation"}
APPROVED_PACKAGE_EXCEPTIONS = {
    "certifi": {
        "MPL-2.0",
        "Mozilla Public License 2.0 (MPL 2.0)",
    },
    "tqdm": {
        "MPL-2.0 AND MIT",
    },
}


def is_approved(raw_license: str) -> bool:
    normalized = raw_license.strip()
    for alias, identifier in ALIASES.items():
        normalized = normalized.replace(alias, identifier)
    if normalized in APPROVED:
        return True
    if re.search(r"(?:^|\W)(?:A?GPL|LGPL|MPL|EPL|CC-BY-SA|NONCOMMERCIAL)(?:\W|$)", normalized, re.I):
        return False
    identifiers = set(re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9.]+)+", normalized))
    identifiers -= IGNORED_WORDS
    return bool(identifiers) and identifiers <= APPROVED


def is_approved_exception(package_name: str, raw_license: str) -> bool:
    return raw_license.strip() in APPROVED_PACKAGE_EXCEPTIONS.get(
        package_name.lower(), set()
    )


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json"],
        check=True,
        capture_output=True,
        text=True,
    )
    packages = json.loads(result.stdout)
    metadata_licenses = {
        distribution.metadata["Name"].lower(): (
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or "UNKNOWN"
        )
        for distribution in distributions()
        if distribution.metadata.get("Name")
    }
    for package in packages:
        if package["License"].strip().upper() == "UNKNOWN":
            package["License"] = metadata_licenses.get(package["Name"].lower(), "UNKNOWN")
        if is_approved_exception(package["Name"], package["License"]):
            package["Policy Exception"] = "approved-commercial-use-exception"
    Path("third-party-licenses.json").write_text(
        json.dumps(packages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rejected = [
        f"{item['Name']}=={item['Version']}: {item['License']}"
        for item in packages
        if not is_approved(item["License"])
        and not is_approved_exception(item["Name"], item["License"])
    ]
    if rejected:
        print("Non-permissive or unknown licenses detected:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in rejected), file=sys.stderr)
        return 1
    exception_count = sum("Policy Exception" in item for item in packages)
    print(
        f"Checked {len(packages)} distributions; all licenses are permitted "
        f"({exception_count} package-specific exceptions)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
