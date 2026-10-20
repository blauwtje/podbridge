import argparse
import importlib
import sys
import traceback

from podbridge import __version__, update, window
from podbridge.ui_state import UiState

COMMANDS = {
    "run": ("podbridge.app", "run"),
    "pair": ("podbridge.app", "pair"),
    "firmware": ("podbridge.app", "firmware"),
    "doctor": ("podbridge.doctor", "main"),
    "restore": ("podbridge.winusb", "restore_main"),
    "selftest": ("podbridge.selftest", "main"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="podbridge")
    parser.add_argument("--version", action="version", version=f"podbridge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        p = sub.add_parser(name)
        p.add_argument("--pause", action="store_true", help="wait for Enter before exit")
        if name in ("run", "pair"):
            p.add_argument("--transport", help="Bluetooth USB device, usb:<vid>:<pid> in hex")
        if name == "run":
            p.add_argument("--output-source", help="capture this device instead of the default render endpoint")
            p.add_argument("--no-update", action="store_true", help="do not look for a newer build")
            p.add_argument("--console", action="store_true", help="skip the window and print to the console")
            p.add_argument("--address", help="AirPods Bluetooth address instead of the one in device.json")
        if name == "restore":
            p.add_argument("--remove-cert", action="store_true", help="also delete the libwdi certificates")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run" and update.apply(args):
        return 0
    module, func = COMMANDS[args.command]
    if args.command == "run" and not args.console:
        ui_state = UiState()
        app = importlib.import_module(module)
        if window.open(lambda: getattr(app, func)(args, reporter=ui_state), ui_state):
            return 0
    try:
        result = getattr(importlib.import_module(module), func)(args)
    except Exception:
        if not args.pause:
            raise
        traceback.print_exc()  # show the error before the window waits, not after it closes
        result = 1
    if args.pause:
        input("Press Enter to close...")
    return result or 0


if __name__ == "__main__":
    sys.exit(main())
