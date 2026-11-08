"""Three-way merge for sheet edits; lists are indivisible inventory/effect units."""

from copy import deepcopy
from app.services.exceptions import ConflictError

_MISSING = object()
TRANSPORT_FIELDS = {'_revision', '_character_id', '_artifact_profile', '_artifact_specials'}


def clean_snapshot(data):
    return {key: deepcopy(value) for key, value in data.items() if key not in TRANSPORT_FIELDS}


def merge_sheet_data(base, edited, current):
    if edited == base:
        return deepcopy(current) if current is not _MISSING else _MISSING
    if current == base or current == edited:
        return deepcopy(edited) if edited is not _MISSING else _MISSING
    if (isinstance(base, dict) or base is _MISSING) and isinstance(edited, dict) and isinstance(current, dict):
        base = {} if base is _MISSING else base
        result = {}
        for key in base.keys() | edited.keys() | current.keys():
            value = merge_sheet_data(base.get(key, _MISSING), edited.get(key, _MISSING), current.get(key, _MISSING))
            if value is not _MISSING:
                result[key] = value
        return result
    raise ConflictError()
