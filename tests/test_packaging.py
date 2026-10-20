import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
ISS = ROOT / "installer" / "podbridge.iss"
BUILD = ROOT / "scripts" / "build_exe.ps1"


def iss_sections() -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in ISS.read_text(encoding="utf-8").splitlines():
        header = re.fullmatch(r"\[(\w+)\]", line.strip())
        if header:
            current = header.group(1)
            sections.setdefault(current, [])
            continue
        if line.strip():
            sections.setdefault(current, []).append(line.strip())
    return sections


def entries(section: str, *parts: str) -> list[str]:
    return [line for line in iss_sections().get(section, []) if all(p in line for p in parts)]


def test_workflow_runs_on_windows_for_tags_and_dispatch():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"tags:\s*\n\s*-\s*['\"]?v\*", text)
    assert "workflow_dispatch:" in text
    assert "runs-on: windows-latest" in text
    assert "contents: write" in text


def test_workflow_builds_every_bundled_part():
    text = WORKFLOW.read_text(encoding="utf-8")
    for part in (
        "scripts/build_exe.ps1",
        "linux-firmware.git/plain/rtl_bt/rtl8852bu_fw.bin",
        "LICENCE.rtlwifi_firmware.txt",
        "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip",
        "https://github.com/pbatard/libwdi",
        "v1.5.1",
        "wdi-simple.exe",
        "ISCC.exe",
        "installer/podbridge.iss",
        "podbridge-setup.exe",
    ):
        assert part in text, part


def test_workflow_attaches_setup_to_release():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "softprops/action-gh-release" in text
    assert re.search(r"files:.*podbridge-setup\.exe", text)


def test_build_script_makes_pyinstaller_onedir():
    text = BUILD.read_text(encoding="utf-8")
    assert "pyinstaller" in text
    assert "--onedir" in text
    assert "--name podbridge" in text
    assert "--collect-submodules podbridge" in text


def test_setup_section():
    setup = iss_sections()["Setup"]
    assert "PrivilegesRequired=admin" in setup
    assert r"DefaultDirName={autopf}\podbridge" in setup
    assert "OutputBaseFilename=podbridge-setup" in setup


def test_installer_bundles_firmware_tools_and_vbcable():
    assert entries("Files", r"firmware\*", r"{app}\firmware")
    assert entries("Files", "LICENCE.rtlwifi_firmware.txt", r"{app}\firmware")
    assert entries("Files", "wdi-simple.exe", r"{app}\tools")
    assert entries("Files", "VBCABLE_Driver_Pack45.zip")


def test_vbcable_notice_names_origin_and_donationware():
    text = ISS.read_text(encoding="utf-8")
    assert "www.vb-cable.com" in text
    assert "donationware" in text.lower()


def test_vbcable_runs_only_when_cable_input_missing():
    run = entries("Run", "VBCABLE_Setup_x64.exe")
    assert len(run) == 1
    assert 'Parameters: "-i -h"' in run[0]
    check = re.search(r"Check:\s*(\w+)", run[0]).group(1)
    code = "\n".join(iss_sections()["Code"])
    assert f"function {check}" in code
    assert r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render" in code
    assert "CABLE Input" in code


def test_restart_only_after_vbcable():
    code = "\n".join(iss_sections()["Code"])
    assert "function NeedRestart" in code
    assert "AlwaysRestart" not in "\n".join(iss_sections()["Setup"])


def test_one_start_menu_shortcut_and_old_ones_removed():
    icons = iss_sections()["Icons"]
    assert len(icons) == 1
    assert entries("Icons", r'"{group}\podbridge"', r"{app}\podbridge.exe", '"run --pause"')
    assert entries("InstallDelete", r"{group}\podbridge doctor.lnk")
    assert entries("InstallDelete", r"{group}\podbridge restore.lnk")


def test_setup_offers_to_start_podbridge():
    run = entries("Run", r"{app}\podbridge.exe", '"run --pause"', "skipifsilent")
    assert len(run) == 1
    assert "postinstall" in run[0]


def test_silent_setup_restarts_podbridge():
    run = entries("Run", r"{app}\podbridge.exe", '"run --pause"', "skipifnotsilent")
    assert len(run) == 1
    assert "nowait" in run[0]


def test_build_is_stamped_with_its_release_tag():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("RELEASE_TAG:") == 1
    assert "refs/tags/" in text.split("RELEASE_TAG:")[1].splitlines()[0]
    assert "tag_name: ${{ env.RELEASE_TAG }}" in text
    assert text.index("dist/podbridge/build.txt") > text.index("build_exe.ps1")


def test_uninstaller_restores_and_removes_cert():
    assert entries("UninstallRun", r"{app}\podbridge.exe", "restore --remove-cert")


def test_firmware_step_fetches_only_the_fw_file():
    # linux-firmware has no rtl8852bu_config.bin, and bumble's --single needs it.
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "bumble-rtk-fw-download" not in text
    assert "rtl8852bu_config.bin" not in text
