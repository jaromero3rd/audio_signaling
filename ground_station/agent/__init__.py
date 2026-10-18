"""Embedded Droid controller, optional Chalk state, and legacy DroidSession stub.

Do not load cloud credentials just to import the local controller.
"""

__all__ = [
    "DroidSession",
    "append_decision",
    "decision_log",
    "get_last_survey",
    "load_mission_state",
    "save_mission_state",
    "set_last_survey",
]


def __getattr__(name: str):
    if name == "DroidSession":
        from ground_station.agent.droid_session import DroidSession

        return DroidSession
    if name in __all__:
        from ground_station.agent import store

        return getattr(store, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
