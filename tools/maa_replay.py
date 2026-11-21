#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Replay native recognition on saved images; every controller input is denied."""

import argparse
import json
from pathlib import Path


def replay(resource_path, image_path, names):
    import cv2
    from maa.controller import CustomController
    from maa.pipeline import JPipelineParser
    from maa.resource import Resource
    from maa.tasker import Tasker

    frame = cv2.imread(str(image_path))
    if frame is None:
        raise ValueError("Cannot read the supplied image")
    frame = cv2.resize(frame, (1280, 720))
    class Offline(CustomController):
        def connect(self):
            return True
        def request_uuid(self):
            return "gbfr-offline-recognition"
        def screencap(self):
            return frame.copy()
        def denied(self, *args, **kwargs):
            raise AssertionError("Offline recognition attempted input")
        start_app = stop_app = click = swipe = touch_down = touch_move = touch_up = denied
        click_key = input_text = key_down = key_up = denied
    resource = Resource()
    resource.use_cpu()
    if not resource.post_bundle(resource_path).wait().succeeded:
        raise ValueError("Native resource loading failed")
    controller = Offline()
    controller.post_connection().wait()
    tasker = Tasker()
    if not tasker.bind(resource, controller):
        raise RuntimeError("Cannot bind offline recognition")
    result = {}
    for name in names:
        raw = resource.get_node_data(name)
        if raw is None:
            raise ValueError("Unknown node: " + name)
        node = JPipelineParser.parse_pipeline_data(raw)
        job = tasker.post_recognition(node.recognition.type, node.recognition.param, frame).wait()
        detail = job.get()
        result[name] = any(n.recognition and n.recognition.hit for n in detail.nodes)
    return result


def main():
    from maa.define import LoggingLevelEnum
    from maa.tasker import Tasker

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resource", type=Path)
    parser.add_argument("image", type=Path)
    parser.add_argument("nodes", nargs="+")
    parser.add_argument("--log-dir", type=Path, default=Path("probe-runs/replay"))
    args = parser.parse_args()
    Tasker.set_log_dir(args.log_dir)
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    print(json.dumps(replay(args.resource, args.image, args.nodes), ensure_ascii=False))


if __name__ == "__main__":
    main()
