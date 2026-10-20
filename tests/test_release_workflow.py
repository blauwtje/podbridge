import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def kernel_org_fetches() -> list[str]:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    return [l for l in lines if "Invoke-WebRequest" in l and re.search(r"FIRMWARE(_LICENCE)?_URL", l)]


def test_kernel_org_downloads_send_a_non_mozilla_user_agent():
    # git.kernel.org answers "Mozilla" agents (PowerShell's default) with a bot-challenge HTML page.
    fetches = kernel_org_fetches()
    assert len(fetches) == 2
    for line in fetches:
        agent = re.search(r"-UserAgent\s+(\S+)", line)
        assert agent, line
        assert "mozilla" not in agent.group(1).lower()


def test_licence_download_rejects_an_html_page():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "LICENCE.rtlwifi_firmware.txt" in text
    assert "StartsWith('<')" in text
