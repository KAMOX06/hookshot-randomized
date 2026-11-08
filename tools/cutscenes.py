"""Cutscene skips. Each patch checks the original before it changes anything, so a game update that moves things
stops a seed instead of breaking a save.

Saria at Link's house (Dm_Kokiri_0045, which Link's house door plays as its OpenEventName): the event gathers the
Kokiri, plays Saria's talk (Dm_Kokiri_0050) and then sets its state. It now runs only: player joins (51) ->
KokiriForest_LinkHouseDoor_Open (45) -> Saria's have-talked (36) -> Link warped outside the door (37) -> camera,
Navi placed (40, 48, 41/50/38/47) -> Dm_Kokiri_0050_After_Near_End (39) -> camera back (43). The skipped talk's
HavePlayedEvent.Dm_Kokiri_0050 is on in a new save, as it would be after watching it (map objects check it).

Saria's ocarina on the bridge (Goto_HyruleCastle::KokiriForest_ArriveLostWood plays Dm_Kokiri_0150, then
Dm_Kokiri_0155): Dm_Kokiri_0150 is a plain timeline (the long talk) and is cut to one frame, so the game still plays
it and records it as played. Dm_Kokiri_0155, which hands over the Fairy Ocarina (EventTriggerAddOrRemovePouchContent),
is kept.

Area intros and Kaepora Gaebora's talks: made instant (instant.py): each outer entry point runs only the flags,
time of day and have-talked it sets, the player's final warp, the area name banner, the fade in and the camera
reset. Kaepora's timeline-only talk (Dm_HrlField_0005) is cut to one frame. Left alone: Kaepora's rides
(Dm_DeathMtTrail_0030/0035, Dm_LakeHrl_0010/0011: the player is carried inside them, and they are optional),
Ganon's Castle's intro (Dm_GanonCastle_0000, a multi-shot scene), and story scenes such as songs.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from evfl import Flow
from timelines import shorten

SARIA_HOUSE = "Event/EventFlow/Dm_Kokiri_0045.bfevfl.zs"
SARIA_BRIDGE = "Event/EventFlow/Dm_Kokiri_0150.bfevfl.zs"
FLAGS = {"HavePlayedEvent.Dm_Kokiri_0050": True}


def _action(f, i):
    a = f.actors()[f.event(i)["actor"]]
    return f"{a['name']}.{a['actions'][f.event(i)['action']]}"


def saria_house(data):
    f = Flow(data)
    out = bytearray(data)
    assert f.entry_points()["EntryPoint0"][0] == 51, "Dm_Kokiri_0045 changed: entry"
    assert _action(f, 51) == "Player_Event.EventTriggerParticipateEvent" and f.event(51)["next"] == 31, "Dm_Kokiri_0045 changed: 51"
    assert _action(f, 45) == "GameDataActor.EventTriggerSetGameDataBool" and f.event(45)["next"] == 0 and \
        f.params(f.event(45)["params"]).get("GameDataName") == "KokiriForest_LinkHouseDoor_Open", "Dm_Kokiri_0045 changed: 45"
    assert _action(f, 36) == "Npc_Kokiri_Saria_Child.EventTriggerSetHaveTalked" and f.event(36)["next"] == 37, "Dm_Kokiri_0045 changed: 36"
    assert _action(f, 37) == "Player_Event.EventPlayerWarpToStarterLinkAnchor", "Dm_Kokiri_0045 changed: 37"
    assert f.params(f.event(39)["params"]).get("GameDataName") == "Dm_Kokiri_0050_After_Near_End", "Dm_Kokiri_0045 changed: 39"
    struct.pack_into("<H", out, f.event(51)["next_field"], 45)
    struct.pack_into("<H", out, f.event(45)["next_field"], 36)
    print("  cutscene: Saria at Link's house skipped (state kept)")
    return bytes(out)


def saria_bridge(data):
    out = shorten(data, 1.0)
    print("  cutscene: Saria's talk on the bridge cut to one frame (ocarina hand-over kept)")
    return out


AREA_INTROS = ["Dm_Deku_0000", "Dm_DeathMtTrail_0000", "Dm_Goron_0000", "Dm_Zora_0000", "Dm_DesertColossus_0000",
               "Dm_LakeHrl_0000", "Dm_HrlCastle_0005", "Dm_DeathMtCrater_0000", "Dm_ZoraFountain_0000"]
KAEPORA_TALKS = ["Dm_HrlField_0010", "Dm_HrlField_0030", "Dm_HrlField_0040", "Dm_HrlField_0050", "Dm_HrlField_0080",
                 "Dm_LostWood_0000", "Dm_LostWood_0010", "Dm_LakeHrl_0003", "Dm_LakeHrl_0005", "Dm_ZoraRiver_0000",
                 "Dm_ZoraRiver_0005", "Dm_HrlCastle_0000"]
KAEPORA_TIMELINES = ["Dm_HrlField_0005"]


def instant(name):
    def patch(data):
        from instant import make_instant
        out = make_instant(data)
        print(f"  cutscene: {name} made instant")
        return out
    return patch


def cut(name):
    def patch(data):
        out = shorten(data, 1.0)
        print(f"  cutscene: {name} cut to one frame")
        return out
    return patch


PATCHES = [(SARIA_HOUSE, saria_house), (SARIA_BRIDGE, saria_bridge)]
PATCHES += [(f"Event/EventFlow/{n}.bfevfl.zs", instant(n)) for n in AREA_INTROS + KAEPORA_TALKS]
PATCHES += [(f"Event/EventFlow/{n}.bfevfl.zs", cut(n)) for n in KAEPORA_TIMELINES]
