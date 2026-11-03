"""JSON files in DATA_DIR: settings, keep limits, and the last automatic run."""
import json
import os
import threading

from .config import DATA_DIR, LAST_RUN_PATH, LIMITS_PATH, SETTINGS_PATH

# Held while changing keep limits and while pruning, so the two never interleave.
data_lock = threading.Lock()


def write_json(path, obj):
    """Write JSON atomically: a crash mid-write leaves the old file, never a half-written one."""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_settings(settings):
    write_json(SETTINGS_PATH, settings)


def load_limits():
    try:
        with open(LIMITS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_limits(limits):
    write_json(LIMITS_PATH, limits)


def load_last_run():
    """The most recent automatic run, saved so it survives restarts (None if there hasn't been one)."""
    try:
        with open(LAST_RUN_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_last_run(entry):
    write_json(LAST_RUN_PATH, entry)
