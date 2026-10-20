"""Podbridge's Realtek firmware download, installed over Bumble's `Driver.download_for_rtl8723b`.

Bumble 0.0.236 sends fragment indexes that differ from Linux's from fragment 128 on, never checks that the
firmware file belongs to the chip, ignores the chip's security key and only warns when no patch fits. This
replacement checks all of that before it sends one fragment, then downloads with Linux's index sequence
(drivers/bluetooth/btrtl.c, rtl_download_firmware).
"""

import logging
from dataclasses import dataclass, field
from typing import ClassVar

from bumble import hci
from bumble.drivers import rtk

log = logging.getLogger("podbridge")

READ_TIMEOUT = 10.0  # Linux HCI_INIT_TIMEOUT; Bumble has no default, so a silent chip would hang forever
PROJECT_ID_8852B = 20
SEC_PROJ = bytes.fromhex("10a4ad00b0")  # btrtl.c RTL_SEC_PROJ register read


class RtkDownloadError(RuntimeError):
    """The download was refused or could not be installed; the message says no firmware was sent."""


@hci.HCI_SyncCommand.sync_command(hci.HCI_GenericReturnParameters)
@dataclass
class HCI_RTK_Read_Register_Command(hci.HCI_SyncCommand[hci.HCI_GenericReturnParameters]):
    op_code: ClassVar[int] = hci.hci_vendor_command_op_code(0x61)
    data: bytes = field(metadata=hci.metadata("*"))


def _refuse(found: str) -> RtkDownloadError:
    return RtkDownloadError(f"{found}; no firmware was sent")


async def _read(host, command):
    try:
        return await host.send_sync_command_raw(command, response_timeout=READ_TIMEOUT)
    except Exception as error:  # a timeout or a rejected command: say which, never send firmware
        raise _refuse(f"{type(error).__name__} reading {command.name}") from error


def _ok(response, params_class) -> bool:
    rp = response.return_parameters
    return isinstance(rp, params_class) and rp.status == hci.HCI_SUCCESS


async def _read_key_id(host) -> int:
    try:
        response = await host.send_sync_command_raw(
            HCI_RTK_Read_Register_Command(data=SEC_PROJ), response_timeout=READ_TIMEOUT
        )
    except Exception as error:
        raise _refuse(f"security key_id unreadable ({type(error).__name__})") from error
    data = getattr(response.return_parameters, "data", None)
    if not isinstance(data, bytes) or len(data) != 3 or data[0] != 0:
        raise _refuse(f"security key_id unreadable (reply {data.hex() if isinstance(data, bytes) else data!r})")
    return data[1]


def _expected_project_id_matches(driver_info, project_id: int) -> bool:
    if driver_info.fw_name == "rtl8852bu_fw.bin":
        return project_id == PROJECT_ID_8852B
    return rtk.RTK_PROJECT_ID_TO_ROM.get(project_id) == driver_info.rom


def fragment_indexes(count: int) -> list[int]:
    """Linux's order: index = j++, j = 1 after index 0x7f, end marker 0x80 on the last fragment."""
    indexes, j = [], 0
    for i in range(count):
        index = j
        j = 1 if index == 0x7F else j + 1
        indexes.append(index | 0x80 if i == count - 1 else index)
    return indexes


async def download_for_rtl8723b(self) -> int | None:
    host = self.host
    local = await _read(host, hci.HCI_Read_Local_Version_Information_Command())
    if not _ok(local, hci.HCI_Read_Local_Version_Information_ReturnParameters):
        raise _refuse("local version unreadable")
    version = local.return_parameters
    log.info(
        "rtk chip: hci_version=0x%02X hci_subversion=0x%04X lmp_subversion=0x%04X",
        int(version.hci_version), version.hci_subversion, version.lmp_subversion,
    )

    rom_version = 0
    if self.driver_info.has_rom_version:
        rom = await _read(host, rtk.HCI_RTK_Read_ROM_Version_Command())
        if not _ok(rom, rtk.HCI_RTK_Read_ROM_Version_ReturnParameters):
            raise _refuse("0xFC6D ROM version unreadable")
        rom_version = rom.return_parameters.version
        log.info("rtk chip: 0xFC6D ROM version reply status=0x%02X rom_version=%d", rom.return_parameters.status, rom_version)

    try:
        firmware = rtk.Firmware(self.firmware)
    except rtk.RtkFirmwareError as error:
        raise _refuse(f"firmware file unusable ({error})") from error
    if not _expected_project_id_matches(self.driver_info, firmware.project_id):
        raise _refuse(
            f"firmware file is for project_id {firmware.project_id} but the chip "
            f"(lmp_subversion 0x{version.lmp_subversion:04X}, {self.driver_info.fw_name}) needs another"
        )

    key_id = await _read_key_id(host)
    log.info("rtk chip: security key_id=%d", key_id)
    if key_id != 0:
        raise _refuse(f"chip has security key_id {key_id}, only key_id 0 is supported")

    patch = next((p for p in firmware.patches if p[0] == rom_version + 1), None)
    if patch is None:
        raise _refuse(f"no patch for rom_version {rom_version} (needs chip_id {rom_version + 1})")

    payload = patch[1] + self.config if self.config else patch[1]
    count = -(-len(payload) // rtk.RTK_FRAGMENT_LENGTH)
    for n, index in enumerate(fragment_indexes(count)):
        start = n * rtk.RTK_FRAGMENT_LENGTH
        await host.send_sync_command(
            rtk.HCI_RTK_Download_Command(index=index, payload=payload[start : start + rtk.RTK_FRAGMENT_LENGTH])
        )
    log.info("rtk download complete: %d fragments", count)

    after = await host.send_sync_command_raw(rtk.HCI_RTK_Read_ROM_Version_Command())
    if _ok(after, rtk.HCI_RTK_Read_ROM_Version_ReturnParameters):
        log.info("rtk ROM version after download: %d", after.return_parameters.version)
    else:
        log.warning("rtk ROM version after download unreadable")
    return firmware.version


def install() -> None:
    """Swap Bumble's Realtek download for ours; fail loudly when the pinned Bumble no longer has it."""
    if not callable(getattr(rtk.Driver, "download_for_rtl8723b", None)):
        raise RtkDownloadError("pinned Bumble has no rtk.Driver.download_for_rtl8723b to replace; no firmware was sent")
    rtk.Driver.download_for_rtl8723b = download_for_rtl8723b
