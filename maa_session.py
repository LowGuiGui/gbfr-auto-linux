# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Quest episode accounting, independent of screenshots and the Maa runtime."""

from dataclasses import asdict, dataclass


class SessionError(RuntimeError):
    pass


@dataclass
class Session:
    quest: str
    limit: int = 1
    completed: int = 0
    episode: int = 0
    phase: str = "ready"
    finish_requested: bool = False
    error: str | None = None

    def __post_init__(self):
        if not self.quest or type(self.limit) is not int or not 1 <= self.limit <= 999:
            raise SessionError("Choose a quest and a successful-run limit from 1 to 999")

    def admit(self):
        if self.phase != "ready" or not self.should_continue:
            raise SessionError("A new quest cannot start in the current session state")
        self.episode += 1
        self.phase = "starting"

    def battle(self):
        if self.phase not in {"starting", "battle"}:
            raise SessionError("An unrelated battle cannot be adopted by this session")
        self.phase = "battle"

    def victory(self):
        if self.phase not in {"battle", "settling"}:
            raise SessionError("A pre-existing result cannot count as a new success")
        self.phase = "settling"

    def settle(self):
        if self.phase != "settling":
            raise SessionError("A success requires an observed victory and settlement")
        self.completed += 1
        self.phase = "ready"

    @property
    def should_continue(self):
        return not self.finish_requested and self.completed < self.limit and not self.error

    def fail(self, reason):
        self.error = str(reason)
        self.phase = "error"

    def snapshot(self):
        return asdict(self)
