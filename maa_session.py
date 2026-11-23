# SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Run accounting for a session built on the game's own Repeat Quest.

Independent of screenshots and the Maa runtime. The first run of a session starts
from the quest the player opened at the counter (`admit`); every later run starts
because the game repeats the quest after the session turned Repeat Quest on
(`repeat_on`), including after 是 on the game's tenth-run dialog (`answer_yes`). A run
counts once, on its results screen, and only after a battle of that run was observed.
With repeat on the game skips the victory screen, so victory is not part of the
boundary.

A session ends in one of three ways: `finished` (the run limit or a finish request
was reached and the game is back in town), `stopped` (the player stopped the
automation; counted runs stand), or `error` (something the session cannot account
for, with its reason).
"""

from dataclasses import asdict, dataclass

PHASES = ("ready", "starting", "battle", "settled", "finished", "stopped", "error")
ENDED = {"finished", "stopped", "error"}


class SessionError(RuntimeError):
    pass


@dataclass
class Session:
    quest: str | None = None  # the quest's name as read on its results page, for display
    limit: int | None = 1  # None: until the player stops it
    completed: int = 0
    episode: int = 0  # runs started, from the quest menus or by the game's repeat
    admissions: int = 0  # runs started from the quest menus (one per session)
    continued: int = 0  # tenth-run dialogs answered 是
    phase: str = "ready"
    repeat: bool = False  # Repeat Quest was verified on after the last settled run
    # Repeat Quest was last seen on in the game. Unlike `repeat` it survives a results
    # screen, because the game keeps the toggle on until it is turned off or reset by
    # the tenth-run dialog; it tells a stopped player whether the game keeps farming.
    left_on: bool | None = False  # None: a toggle input was sent, its result not read yet
    cap_seen: bool = False  # the tenth-run dialog showed here and was not answered with 是
    finish_requested: bool = False
    error: str | None = None

    def __post_init__(self):
        if self.limit is not None and (type(self.limit) is not int or not 1 <= self.limit <= 999):
            raise SessionError("Choose a successful-run limit from 1 to 999, or run until stopped")

    @property
    def should_continue(self):
        if self.error or self.finish_requested or self.phase == "stopped":
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

    def uncount(self):
        """The results page this run was counted on showed a failure line afterwards: the
        quest failed, so the count is taken back. The hooks ask for it only shortly after
        the count (maa_agent.LATE_FAILURE_S), so a later run's failure page, even one whose
        battle went unseen, never takes back an earlier success."""
        if self.phase != "settled" or self.completed < 1:
            raise SessionError("A count can only be taken back on the page it was made on")
        self.completed -= 1
        self.phase = "battle"
        self.repeat = False

    def repeat_on(self):
        """The results page reads Repeat Quest on. The pipeline reads it while the session
        goes on; a finish request that lands between that read and this record changes
        nothing about the toggle, so it is recorded all the same and turned off next."""
        if self.phase != "settled":
            raise SessionError("Repeat Quest was read on outside a results screen")
        self.repeat = self.left_on = True

    def repeat_off(self):
        if self.phase != "settled":
            raise SessionError("Repeat Quest can only be turned off on a results screen")
        self.repeat = self.left_on = False

    def tenth_run(self):
        """The game's tenth-run dialog: it reset Repeat Quest and asks whether to go on."""
        if self.phase != "settled":
            raise SessionError("The tenth-run dialog appeared outside a results screen")
        self.repeat = self.left_on = False
        self.cap_seen = True

    def answer_yes(self):
        """是 on the tenth-run dialog: the game turns Repeat Quest back on and goes on.

        Repeat is believed on only once the results page reads it (`repeat_on`)."""
        if self.phase != "settled" or not self.should_continue:
            raise SessionError("The tenth-run dialog may only be answered to continue the session")
        self.cap_seen = False
        self.continued += 1
        self.left_on = None

    def toggle_sent(self):
        """A key that switches Repeat Quest was sent; until a page reads the toggle back,
        whether the game will start another run is unknown."""
        self.left_on = None

    def finish(self):
        if self.phase not in {"ready", "settled"} or self.repeat or self.should_continue:
            raise SessionError("The session did not reach its settlement boundary")
        self.phase = "finished"

    def stop(self):
        """The player stopped the automation before the session finished."""
        if self.phase not in ENDED:
            self.phase = "stopped"

    def fail(self, reason):
        self.error = str(reason)
        self.phase = "error"

    def snapshot(self):
        return dict(asdict(self), overrun=self.overrun)
