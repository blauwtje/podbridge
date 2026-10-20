"""Bond recovery: drop a link key the AirPods rejected and ask for a re-pair."""

import json
import os
from pathlib import Path

from bumble.hci import (
    HCI_AUTHENTICATION_FAILURE_ERROR,
    HCI_PIN_OR_KEY_MISSING_ERROR,
    HCI_Error,
)
from bumble.keys import JsonKeyStore

from podbridge import host
from podbridge.events import Event

AUTH_FAILURES = (HCI_AUTHENTICATION_FAILURE_ERROR, HCI_PIN_OR_KEY_MISSING_ERROR)


class RePairNeeded(Exception):
    """The stored link key is gone; run `podbridge pair` again."""


def is_auth_failure(exc) -> bool:
    return isinstance(exc, HCI_Error) and exc.error_code in AUTH_FAILURES


def drop_key(path, address) -> None:
    """Rewrite keys.json without `address`; a missing or unreadable file counts as dropped."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    wanted = str(address).upper()
    for peers in data.values():
        if not isinstance(peers, dict):
            continue
        for name in [n for n in peers if n.upper() == wanted or n.upper().startswith(wanted + "/")]:
            del peers[name]
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=4))
    os.replace(temp, path)


def key_saved(path, address) -> bool:
    """True when keys.json holds a BR/EDR link key for `address` where Bumble will read it.

    The host opens the store as JsonKeyStore(None, path): the `__DEFAULT__` namespace, else
    the only namespace. A file Bumble cannot load is renamed to keys.json.rejected, because
    Bumble then fails every key update and pairing would never store a key.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except ValueError:
        data = None
    if isinstance(data, dict) and JsonKeyStore.DEFAULT_NAMESPACE in data:
        peers = data[JsonKeyStore.DEFAULT_NAMESPACE]
    elif isinstance(data, dict) and len(data) == 1:
        peers = next(iter(data.values()))
    elif isinstance(data, dict):
        return False
    else:
        peers = None
    if not isinstance(peers, dict):
        os.replace(path, path.with_name(path.name + ".rejected"))
        return False
    wanted = str(address).upper()
    return any(
        (name.upper() == wanted or name.upper().startswith(wanted + "/"))
        and isinstance(keys, dict)
        and "link_key" in keys
        for name, keys in peers.items()
    )


async def connect_bonded(host_, address, reporter):
    """Reconnect with the stored key; on a rejected key drop it, tell the user and raise RePairNeeded."""
    try:
        return await host_.connect(address)
    except HCI_Error as exc:
        if not is_auth_failure(exc):
            raise
        drop_key(host.keys_path(), address)
        step = "re-pair the AirPods in podbridge, hold the case button"
        reporter.emit(Event("bond_dropped", "The AirPods no longer accept the saved pairing.", step))
        raise RePairNeeded(step) from exc
