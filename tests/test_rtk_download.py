"""The Realtek firmware download podbridge installs over Bumble's, driven through Bumble's real Driver class."""

import asyncio
import logging
import struct

import pytest
from bumble import hci
from bumble.drivers import rtk

from podbridge import host, rtk_download

DOWNLOAD = hci.hci_vendor_command_op_code(0x20)
READ_ROM_VERSION = hci.hci_vendor_command_op_code(0x6D)
READ_REGISTER = hci.hci_vendor_command_op_code(0x61)
READ_LOCAL_VERSION = hci.HCI_READ_LOCAL_VERSION_INFORMATION_COMMAND

FRAGMENT = 252
SEC_PROJ_READ = bytes.fromhex("10a4ad00b0")
PROJECT_ID_8852B = 20


def epatch(project_id, patches, version=0x11223344):
    """A firmware image Bumble's Firmware parses: `patches` is a list of (chip_id, patch_length)."""
    count = len(patches)
    body = b"".join(bytes([n % 251]) for n in range(1000))
    offset = 14 + 8 * count
    chip_ids, lengths, offsets, blobs = b"", b"", b"", b""
    for chip_id, length in patches:
        chip_ids += struct.pack("<H", chip_id)
        lengths += struct.pack("<H", length)
        offsets += struct.pack("<I", offset + len(blobs))
        blobs += (body * (length // len(body) + 1))[:length]
    tail = bytes([project_id, 0x01, 0x00]) + bytes([0x51, 0x04, 0xFD, 0x77])
    return b"Realtech" + struct.pack("<IH", version, count) + chip_ids + lengths + offsets + blobs + tail


def linux_indexes(count):
    """btrtl.c rtl_download_firmware: j starts at 0, index = j++, j = 1 after index 0x7f, 0x80 on the last."""
    indexes, j = [], 0
    for i in range(count):
        index = j
        j += 1
        if index == 0x7F:
            j = 1
        if i == count - 1:
            index |= 0x80
        indexes.append(index)
    return indexes


class FakeHost:
    """Records every command the driver sends and answers reads like an RTL8852B in ROM state."""

    def __init__(self, rom_version=1, key_id=0, key_reply="ok"):
        self.rom_version = rom_version
        self.key_id = key_id
        self.key_reply = key_reply
        self.sent = []

    async def send_sync_command_raw(self, command, response_timeout=None):
        self.sent.append(command)
        if command.op_code == READ_LOCAL_VERSION:
            rp = hci.HCI_Read_Local_Version_Information_ReturnParameters(
                status=hci.HCI_SUCCESS,
                hci_version=hci.SpecificationVersion.BLUETOOTH_CORE_5_2,
                hci_subversion=0x000B,
                lmp_version=hci.SpecificationVersion.BLUETOOTH_CORE_5_2,
                company_identifier=0x005D,
                lmp_subversion=0x8852,
            )
        elif command.op_code == READ_ROM_VERSION:
            rp = rtk.HCI_RTK_Read_ROM_Version_ReturnParameters(status=hci.HCI_SUCCESS, version=self.rom_version)
        elif command.op_code == READ_REGISTER:
            if self.key_reply == "timeout":
                raise asyncio.TimeoutError
            status = 0 if self.key_reply == "ok" else 1
            rp = hci.HCI_GenericReturnParameters(data=bytes([status, self.key_id, 0]))
        else:
            raise AssertionError(f"unexpected command {command!r}")
        return type("Response", (), {"return_parameters": rp})()

    async def send_sync_command(self, command, response_timeout=None):
        self.sent.append(command)
        assert command.op_code == DOWNLOAD, f"unexpected command {command!r}"
        return rtk.HCI_RTK_Download_ReturnParameters(status=hci.HCI_SUCCESS, index=command.index)

    def opcodes(self):
        return [command.op_code for command in self.sent]

    def download_indexes(self):
        return [command.index for command in self.sent if command.op_code == DOWNLOAD]


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(rtk.Driver, "download_for_rtl8723b", rtk.Driver.download_for_rtl8723b)
    rtk_download.install()


def driver_for(fake, firmware, config=None):
    info = next(i for i in rtk.Driver.DRIVER_INFOS if i.fw_name == "rtl8852bu_fw.bin")
    return rtk.Driver(fake, info, firmware, config)


def download(fake, firmware, config=None):
    return asyncio.run(driver_for(fake, firmware, config).download_for_rtl8723b())


def test_231_fragments_use_the_linux_index_sequence(installed):
    fake = FakeHost()
    download(fake, epatch(PROJECT_ID_8852B, [(1, 20000), (2, 57997)]))
    indexes = fake.download_indexes()
    assert len(indexes) == 231
    assert indexes == linux_indexes(231)
    assert indexes[127] == 127 and indexes[128] == 1 and indexes[-1] == 0x80 | 103


def test_65_fragments_keep_their_indexes(installed):
    fake = FakeHost()
    download(fake, epatch(PROJECT_ID_8852B, [(1, 16200), (2, 16200)]))
    indexes = fake.download_indexes()
    assert indexes == list(range(64)) + [0x80 | 64]
    assert indexes == linux_indexes(65)


def test_config_is_appended_and_rom_version_read_after_download(installed):
    fake = FakeHost()
    firmware = epatch(PROJECT_ID_8852B, [(2, 1000)])
    result = download(fake, firmware, config=b"\xaa" * 300)
    sent = b"".join(c.payload for c in fake.sent if c.op_code == DOWNLOAD)
    assert len(sent) == 1000 + 300
    assert sent.endswith(b"\xaa" * 300)
    assert fake.opcodes()[-1] == READ_ROM_VERSION
    assert fake.opcodes().count(DOWNLOAD) == math_ceil(1300)
    assert result == 0x11223344


def math_ceil(size):
    return -(-size // FRAGMENT)


def test_logs_chip_version_rom_version_and_key_id_before_any_fragment(installed, caplog):
    caplog.set_level(logging.INFO, logger="podbridge")
    fake = FakeHost(rom_version=1, key_id=0)
    download(fake, epatch(PROJECT_ID_8852B, [(2, 1000)]))
    lines = [r.getMessage() for r in caplog.records if r.name == "podbridge" and r.levelno == logging.INFO]
    text = "\n".join(lines)
    assert "hci_version=0x0B" in text
    assert "hci_subversion=0x000B" in text
    assert "lmp_subversion=0x8852" in text
    assert "rom_version=1" in text
    assert "key_id=0" in text


REFUSALS = [
    pytest.param(dict(project_id=18), {}, "project_id 18", {READ_REGISTER}, id="project-id-mismatch"),
    pytest.param(dict(), dict(key_id=1), "key_id 1", set(), id="key-id-nonzero"),
    pytest.param(dict(), dict(key_reply="timeout"), "key_id", set(), id="key-read-times-out"),
    pytest.param(dict(), dict(key_reply="bad-status"), "key_id", set(), id="key-read-fails"),
    pytest.param(dict(), dict(rom_version=5), "rom_version 5", set(), id="no-patch-for-rom"),
]


@pytest.mark.parametrize("firmware_args, host_args, found, never_sent", REFUSALS)
def test_refusal_names_what_was_found_and_sends_no_download(installed, firmware_args, host_args, found, never_sent):
    fake = FakeHost(**host_args)
    firmware = epatch(firmware_args.get("project_id", PROJECT_ID_8852B), [(2, 5000)])
    with pytest.raises(rtk_download.RtkDownloadError) as refused:
        download(fake, firmware)
    assert found in str(refused.value)
    assert "no firmware was sent" in str(refused.value)
    assert DOWNLOAD not in fake.opcodes()
    assert not (never_sent & set(fake.opcodes()))


def test_unparseable_firmware_is_a_refusal_too(installed):
    fake = FakeHost()
    with pytest.raises(rtk_download.RtkDownloadError, match="no firmware was sent"):
        download(fake, b"not a firmware")
    assert DOWNLOAD not in fake.opcodes()


def test_security_key_is_read_with_the_linux_register_command(installed):
    fake = FakeHost()
    download(fake, epatch(PROJECT_ID_8852B, [(2, 1000)]))
    reads = [c for c in fake.sent if c.op_code == READ_REGISTER]
    assert len(reads) == 1
    assert bytes(reads[0])[4:] == SEC_PROJ_READ


def test_install_fails_loudly_when_the_pinned_bumble_method_is_missing(monkeypatch):
    monkeypatch.delattr(rtk.Driver, "download_for_rtl8723b")
    with pytest.raises(rtk_download.RtkDownloadError, match="download_for_rtl8723b"):
        rtk_download.install()


def test_host_start_installs_the_download_before_the_transport_opens(monkeypatch):
    order = []

    class Stop(Exception): ...

    async def open_transport(name):
        order.append("open")
        raise Stop

    monkeypatch.setattr(host, "use_firmware_folder", lambda: order.append("folder"))
    monkeypatch.setattr(rtk_download, "install", lambda: order.append("install"))
    monkeypatch.setattr(host, "open_transport", open_transport)
    with pytest.raises(Stop):
        asyncio.run(host.Host("usb:0").start())
    assert order == ["folder", "install", "open"]
