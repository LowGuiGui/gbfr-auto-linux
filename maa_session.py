# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Run accounting for a session built on the game's own Repeat Quest.

Independent of screenshots and the Maa runtime. The first run of a session starts
from the quest menus (`admit`); later runs start because the game repeats the quest
after the session turned Repeat Quest on (`repeat_on`). A run counts once, on its
results screen, and only after a battle of that run was observed. With repeat on the
game skips the victory screen, so victory is not part of the boundary.
"""

from dataclasses import asdict, dataclass

PHASES = ("ready", "starting", "battle", "settled", "finished", "error")


class SessionError(RuntimeError):
    pass


@dataclass
class Session:
    quest: str
    limit: int | None = 1  # None: until the player stops it
    completed: int = 0
    episode: int = 0  # runs started, from the menus or by the game's repeat
    admissions: int = 0  # runs started from the quest menus
    phase: str = "ready"
    repeat: bool = False  # Repeat Quest was verified on after the last settled run
    cap_seen: bool = False  # the tenth-run dialog showed on the current results screen
    finish_requested: bool = False
    error: str | None = None

    def __post_init__(self):
        if not self.quest:
            raise SessionError("Choose a quest")
        if self.limit is not None and (type(self.limit) is not int or not 1 <= self.limit <= 999):
            raise SessionError("Choose a successful-run limit from 1 to 999, or run until stopped")

    @property
    def should_continue(self):
        if self.error or self.finish_requested:
            return False
        return self.limit is None or self.completed < self.limit

    @property
    def overrun(self):
        """Runs completed beyond the limit, because the next run had already started."""
        return 0 if self.limit is None else max(0, self.completed - self.limit)

    def admit(self):
        if self.phase != "ready" or self.repeat or not self.should_continue:
            raise SessionError("A new quest cannot start in the current session state")
        self.episode += 1
        self.admissions += 1
        self.phase = "starting"

    def battle(self):
        if self.phase in {"starting", "battle"}:
            self.phase = "battle"
        elif self.phase == "settled" and self.repeat:
            # The game repeated the quest. If a stop arrived after Repeat Quest was
            # left on, this run is still counted and reported as an overrun.
            self.episode += 1
            self.phase = "battle"
        elif self.phase == "settled":
            raise SessionError("Repeat Quest was off, yet the game started another run")
        else:
            raise SessionError("An unrelated battle cannot be adopted by this session")

    def settle(self):
        if self.phase != "battle":
            raise SessionError("A success requires a battle observed before its results")
        self.completed += 1
        self.phase = "settled"
        self.repeat = False
        self.cap_seen = False

    def repeat_on(self):
        if self.phase != "settled" or not self.should_continue:
            raise SessionError("Repeat Quest may only be turned on to continue the session")
        self.repeat = True

    def repeat_off(self):
        if self.phase != "settled":
            raise SessionError("Repeat Quest can only be turned off on a results screen")
        self.repeat = False

    def tenth_run(self):
        """The game's tenth-run dialog: it reset Repeat Quest and asks whether to go on."""
        if self.phase != "settled":
            raise SessionError("The tenth-run dialog appeared outside a results screen")
        self.repeat = False
        self.cap_seen = True

    def town(self):
        """Back in town after a results screen without finishing: start from the menus."""
        if self.phase != "settled" or self.repeat or not self.should_continue:
            raise SessionError("The session cannot restart from town in its current state")
        self.phase = "ready"
        self.cap_seen = False

    def finish(self):
        if self.phase not in {"ready", "settled"} or self.repeat or self.should_continue:
            raise SessionError("The session did not reach its settlement boundary")
        self.phase = "finished"

    def fail(self, reason):
        self.error = str(reason)
        self.phase = "error"

    def snapshot(self):
        return dict(asdict(self), overrun=self.overrun)
