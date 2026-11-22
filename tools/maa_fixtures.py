#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Make a recognition fixture: a 1280x720 JPEG blacked out outside the leaf regions.

Only the regions that the checked `GBFR_See_*` leaves (and catalog nodes they reference) read
survive, so a fixture keeps what recognition needs and drops the rest of the screen,
including anything personal. The regions come from the resource itself, so a fixture
made today stays valid while those regions do.
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "assets/resource/pipeline/farming.json"
# The leaves the fixtures check. The defeat and settlement-dialog leaves read large
# central regions and have no measured frames yet, so they are left out to keep
# fixtures small.
LEAVES = ("GBFR_See_Results", "GBFR_See_RepeatOff", "GBFR_See_RepeatOn", "GBFR_See_TenthRun",
          "GBFR_See_Battle", "GBFR_See_Victory", "GBFR_See_Town", "GBFR_See_Tips")


def regions(pipeline, leaves=LEAVES):
    """Every ROI the given leaves can read, following named references."""
    found, seen = [], set()

    def visit(value):
        if isinstance(value, str):
            if value not in seen and value in pipeline:
                seen.add(value)
                visit(pipeline[value])
        elif isinstance(value, dict):
            if "roi" in value:
                found.append(tuple(value["roi"]))
            for child in value.get("all_of", []) + value.get("any_of", []):
                visit(child)
    for name in leaves:
        visit(name)
    return sorted(set(found))


def mask(frame, rois):
    import numpy as np

    result = np.zeros_like(frame)
    for x, y, w, h in rois:
        result[y:y + h, x:x + w] = frame[y:y + h, x:x + w]
    return result


def make(source, output, pipeline=None):
    import cv2

    frame = cv2.imread(str(source))
    if frame is None:
        raise ValueError("Cannot read the supplied image")
    frame = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
    pipeline = pipeline or json.loads(PIPELINE.read_text())
    if not cv2.imwrite(str(output), mask(frame, regions(pipeline)), [cv2.IMWRITE_JPEG_QUALITY, 90]):
        raise ValueError("Cannot write the fixture")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    make(args.source, args.output)


if __name__ == "__main__":
    main()
