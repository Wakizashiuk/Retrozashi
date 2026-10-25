from retrozashi.emulators.ini_store import IniDocument

SAMPLE = "[EmuCore/GS]\r\nRenderer = -1\r\nupscale_multiplier = 1\r\n\r\n[UI]\r\nStartFullscreen = false\r\n"


def test_get_set_preserves_case_and_newlines(tmp_path):
    p = tmp_path / "PCSX2.ini"
    p.write_bytes(SAMPLE.encode())
    doc = IniDocument.load(p)
    assert doc.get("EmuCore/GS", "upscale_multiplier") == "1"
    assert doc.set("EmuCore/GS", "upscale_multiplier", "6") == "1"
    assert doc.set("EmuCore/GS", "VsyncEnable", "true") is None   # new key appended in-section
    assert doc.set("Pad1", "Type", "DualShock2") is None          # new section appended
    bak = doc.save(p, backup=True)
    assert bak and bak.read_bytes() == SAMPLE.encode()
    out = p.read_bytes().decode()
    assert "upscale_multiplier = 6\r\nVsyncEnable = true\r\n\r\n[UI]" in out
    assert out.endswith("[Pad1]\r\nType = DualShock2\r\n")
    assert IniDocument.load(p).section("UI") == {"StartFullscreen": "false"}


def test_qt_style_keys():
    doc = IniDocument("[Renderer]\nresolution_setup\\default=true\nresolution_setup=2\n")
    assert doc.get("Renderer", "resolution_setup") == "2"
    doc.set("Renderer", "resolution_setup\\default", "false")
    assert "resolution_setup\\default=false" in doc.lines
