"""Seed maps from ISRT-era defaults (maps only; RCON shortcuts are hardcoded per type)."""

# (alias, map_name, day, night, checkpoint, checkpoint_ins, self_added)
VANILLA_CHECKPOINT_MAPS = [
    ("Precinct", "Precinct"),
    ("Tell", "Tell"),
    ("Crossing", "Canyon"),
    ("Farmhouse", "Farmhouse"),
    ("Refinery", "Oilfield"),
    ("Hideout", "Town"),
    ("Outskirts", "Compound"),
    ("Ministry", "Ministry"),
    ("Power Plant", "PowerPlant"),
    ("Summit", "Mountain"),
    ("Tideway", "Buhriz"),
    ("Hillside", "Sinjar"),
    ("Bab", "Bab"),
    ("Citadel", "Citadel"),
    ("Gap", "Gap"),
    ("Prison", "Prison"),
]

CUSTOM_CHECKPOINT_MAPS = [
    ("Last Light", "LastLight"),
    ("Trainyard", "Trainyard"),
    ("Hold", "Hold"),
    ("Forest", "Forest"),
]

def checkpoint_scenario(map_alias: str, side: str) -> str:
    # Crossing alias uses Scenario_Crossing_... while map_name is Canyon
    scenario_base = map_alias.replace(" ", "")
    # special cases from ISRT
    alias_to_scenario = {
        "PowerPlant": "PowerPlant",
        "Power Plant": "PowerPlant",
        "Last Light": "LastLight",
        "LastLight": "LastLight",
    }
    base = alias_to_scenario.get(map_alias, scenario_base)
    # Prefer alias-based Scenario names as in ISRT DB
    # Crossing -> Scenario_Crossing_Checkpoint_Security (alias Crossing)
    key = map_alias.replace(" ", "")
    if map_alias == "Crossing":
        key = "Crossing"
    elif map_alias == "Power Plant":
        key = "PowerPlant"
    elif map_alias == "Last Light":
        key = "LastLight"
    if side == "security":
        return f"Scenario_{key}_Checkpoint_Security"
    return f"Scenario_{key}_Checkpoint_Insurgents"
