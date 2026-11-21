<!-- SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui> -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Progression goals and automation scope

Reviewed on 2026-11-21 for the 2.0.6 screen profile. A growth goal is not a claim
that every quest associated with it is automated. The executable list and evidence
status are in `catalog.json` in a built directory (`assets/catalog.json` in the
source tree); the client exposes only those entries.

| Player situation | Useful goal | Software boundary |
|---|---|---|
| Story and Chapter 0 | Unlock the next difficulty, develop the main character and AI party | Story navigation and automatic equipment changes are outside this release. |
| Maniac preparation | Gather the specific boss materials needed for a selected upgrade; develop the party | The first catalog focuses on two repeatable boss-material routes, with ordinary experience/mastery as secondary rewards. It does not claim these are the fastest leveling routes. |
| Proud and Terminus preparation | Identify the missing weapon, awakening and sigil materials for the current version | Add separate, measured quest presets. Do not assume a dated random-drop guide describes the current acquisition system. |
| Expansion progression | Develop the unlocked expansion systems and gather their materials | Confirm the actual unlocks and selected quest in-game before adding a route. Owning DLC alone does not establish progress. |
| Long-term optimization | Stop at an inventory target or plan a material deficit | Requires reliable inventory evidence and a separate design; run counts currently mean successful quests, not items owned. |

The [official systems page](https://relink-ragnarok.granbluefantasy.com/en/systems/)
describes expanded Assist/Full Assist availability, Conflux progression and Master
Traits for max-level characters. Full Assist plus a suitable AI party is therefore
the first strategy to evaluate for each route. Randomized Conflux navigation,
special battle mechanics and every high-difficulty encounter are not implied by
that availability. The [official update index](https://relink-ragnarok.granbluefantasy.com/en/updates/)
is the version reference when revisiting guidance.

The community [Maniac quest guide](https://origin.ludo.guide/guide/granblue-fantasy-relink/quests-maniac)
identifies Landbeast Plate/Horn for Rumble in the Desert and Frostbeast Plate/Horn
for Armor Is Best Served Cold. The localized names used by this profile are
解决沙漠狂徒 and 我要打造冰冷的铠甲. This is a source for reward purpose, not a measured
drop rate or a guarantee that every reward appears each run. Check the selected
quest's current reward display before spending time on a particular material.

For each new preset, record the visible quest and objective, unlock requirements,
target materials, party/settings, supported starts, battle interactions, finish
screen and live evidence. Measure successful yield over total session time before
calling one route optimal. Keep those data separate from shared navigation and
successful-run accounting so progression expansion does not duplicate the runtime.
