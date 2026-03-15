from __future__ import annotations

from shogiarena._core.platform.host_probe.cpu_detection import detect_target_cpu
from shogiarena._core.shared.kernel.cpuinfo_parsing.mapping import map_info_to_target_cpu
from shogiarena._core.shared.kernel.cpuinfo_parsing.parser import parse_linux_cpuinfo_text


def test_parse_linux_cpuinfo_text_extracts_core_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        "shogiarena._core.shared.kernel.cpuinfo_parsing.parser.platform.machine",
        lambda: "x86_64",
    )
    text = """
processor   : 0
vendor_id   : AuthenticAMD
cpu family  : 25
model       : 80
model name  : AMD Ryzen 9 5950X
flags       : sse4_2 avx2 avxvnni
""".strip()

    info = parse_linux_cpuinfo_text(text)

    assert info["arch"] == "X86_64"
    assert info["vendor_id_raw"] == "AuthenticAMD"
    assert info["family"] == 25
    assert info["model"] == 80
    assert info["brand_raw"] == "AMD Ryzen 9 5950X"
    assert "avx2" in info["flags"]
    assert "avx_vnni" in info["flags"]


def test_map_info_to_target_cpu_detects_zen3() -> None:
    result = map_info_to_target_cpu(
        {
            "system": "Linux",
            "arch_string_raw": "x86_64",
            "vendor_id_raw": "AuthenticAMD",
            "family": 25,
            "model": 80,
            "flags": ["sse4_2", "avx2"],
        }
    )

    assert result == "ZEN3"


def test_map_info_to_target_cpu_detects_apple_silicon() -> None:
    result = map_info_to_target_cpu(
        {
            "system": "Darwin",
            "arch_string_raw": "arm64",
            "flags": [],
        }
    )

    assert result == "APPLEM1"


def test_detect_target_cpu_uses_probe_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "shogiarena._core.platform.host_probe.cpu_detection.get_cpu_info",
        lambda: {
            "system": "Linux",
            "arch_string_raw": "x86_64",
            "flags": ["avx512f"],
        },
    )

    assert detect_target_cpu() == "AVX512"
