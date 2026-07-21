"""ネットワークホストの分類ヘルパー。"""

from __future__ import annotations

from ipaddress import ip_address


def normalize_host_name(value: str) -> str:
    """ポリシー比較用にホスト名または IP リテラルを正規化する。"""

    normalized = value.strip().lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized.endswith("."):
        normalized = normalized[:-1]
    return normalized


def is_loopback_host(value: str) -> bool:
    """``value`` が明示的なループバックホストかを返す。"""

    normalized = normalize_host_name(value)
    if normalized == "localhost":
        return True
    if not normalized or "%" in normalized:
        return False
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


__all__ = ["is_loopback_host", "normalize_host_name"]
