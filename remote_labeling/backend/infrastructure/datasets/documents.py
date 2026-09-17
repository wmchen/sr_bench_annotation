"""Desktop-compatible documents shared by direct saves and exports."""

import json
from typing import Any

from ...domain.rules import completion


def json_text(payload: Any) -> str:
    """Format file JSON with desktop-style indentation and a final newline."""
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
        + "\n"
    )


def document(
    sample: str, variant: str, group: dict, dimensions: dict, attribute: str
) -> dict:
    """Build one variant without discarding region extension fields."""
    width, height = dimensions[variant]
    return {
        "version": "realisr-remote-0.1.0",
        "flags": {},
        "checked": not completion(group)["missing"],
        "shapes": group[variant],
        "imagePath": sample,
        "imageData": None,
        "imageHeight": height,
        "imageWidth": width,
        "realisr": {
            "schema_version": 3,
            "attribute": attribute,
            "variant": variant,
            "master": "HR",
        },
    }
