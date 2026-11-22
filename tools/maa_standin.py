# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Animated X11 receiver used only inside the owned headless Gamescope test."""

import argparse
import json
import os
import signal
import tempfile
import time
from pathlib import Path

TEST_APPID = "900000001"


def write_state(path, state):
    """Publish a complete sample using an exclusively created temporary file."""
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sample_input(d, window, events, names, button_mask):
    """Drain events queued by the state-query replies before publishing the sample.

    Python-Xlib can enqueue a release while reading a synchronous reply. Reading
    events only before the queries would log that release one polling tick after
    the first clear state, reversing their apparent observation order.
    """
    keys = list(d.query_keymap())
    pointer = window.query_pointer()
    while d.pending_events():
        event = d.next_event()
        if event.type in names:
            row = {"event": names[event.type], "code": event.detail,
                   "time": time.monotonic(), "state": event.state,
                   "server_time": getattr(event, "time", None)}
            events.write(json.dumps(row) + "\n")
    return {"time": time.monotonic(), "keys": keys, "buttons": pointer.mask & button_mask}


def main():
    from Xlib import X, Xatom, display

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    if os.environ.get("SteamAppId") != TEST_APPID:
        parser.error("the receiver requires the isolated test identity")
    if not os.environ.get("GAMESCOPE_WAYLAND_DISPLAY"):
        parser.error("the receiver must run inside the owned Gamescope")
    d = display.Display()
    root = d.screen().root
    window = root.create_window(
        0, 0, 640, 360, 0, d.screen().root_depth,
        event_mask=X.ExposureMask | X.KeyPressMask | X.KeyReleaseMask
        | X.ButtonPressMask | X.ButtonReleaseMask,
        background_pixel=0x18304A,
    )
    window.set_wm_name("Maa compatibility receiver")
    window.set_wm_class("maa-probe", f"steam_app_{TEST_APPID}")
    window.change_property(d.intern_atom("STEAM_GAME"), Xatom.CARDINAL, 32, [int(TEST_APPID)])
    window.map()
    d.sync()
    gc = window.create_gc(foreground=0xF5CF65)
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    ready = {
        "pid": os.getpid(), "window": window.id, "token": args.token,
        "display": os.environ["DISPLAY"], "appid": TEST_APPID,
        "wayland": os.environ["GAMESCOPE_WAYLAND_DISPLAY"],
    }
    (args.out / "receiver.json").write_text(json.dumps(ready))
    names = {X.KeyPress: "key_down", X.KeyRelease: "key_up",
             X.ButtonPress: "button_down", X.ButtonRelease: "button_up"}
    frame = 0
    with (args.out / "received.jsonl").open("w", buffering=1) as events:
        while running:
            # A moving bar and binary counter make repeated frames detectable.
            gc.change(foreground=0x18304A)
            window.fill_rectangle(gc, 0, 0, 640, 360)
            gc.change(foreground=0xF5CF65)
            window.fill_rectangle(gc, (frame * 7) % 580, 70, 60, 200)
            for bit in range(16):
                if frame & (1 << bit):
                    window.fill_rectangle(gc, 10 + bit * 36, 12, 24, 30)
            # Read-only state gives evidence of stranded keys after disconnect.
            state = sample_input(d, window, events, names,
                                 X.Button1Mask | X.Button2Mask | X.Button3Mask
                                 | X.Button4Mask | X.Button5Mask)
            write_state(args.out / "receiver-state.json", {
                **state, "frame": frame,
            })
            d.flush()
            frame += 1
            time.sleep(1 / 30)
    window.destroy()
    d.close()


if __name__ == "__main__":
    main()
