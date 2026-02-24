"""Arena サービス層 TypedDict 定義のテスト。

SprtStateSnapshot, OpenBenchCounters, および EngineIoTailEntry の
構造的整合性を検証する。
"""

from __future__ import annotations

from shogiarena.arena.services.openbench import OpenBenchCounters
from shogiarena.arena.services.statistics.sprt import SprtStateSnapshot
from shogiarena.utils.types.snapshots import EngineIoTailEntry
from shogiarena.web.dashboard.backend.types import GamesSnapshotPayload


def _required_keys(td: type) -> set[str]:
    """TypedDict の必須キーを返す。"""
    return set(td.__required_keys__)


def _optional_keys(td: type) -> set[str]:
    """TypedDict のオプショナルキーを返す。"""
    return set(td.__optional_keys__)


# ---------------------------------------------------------------------------
# SprtStateSnapshot
# ---------------------------------------------------------------------------


class TestSprtStateSnapshot:
    def test_all_required(self) -> None:
        assert _required_keys(SprtStateSnapshot) == {
            "elo0",
            "elo1",
            "alpha",
            "beta",
            "wins",
            "draws",
            "losses",
            "games_played",
            "llr",
        }

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(SprtStateSnapshot) == set()

    def test_key_count(self) -> None:
        assert len(_required_keys(SprtStateSnapshot)) == 9


# ---------------------------------------------------------------------------
# OpenBenchCounters output validation (private TypedDicts tested indirectly)
# ---------------------------------------------------------------------------


class TestOpenBenchCountersPayload:
    def test_to_payload_keys(self) -> None:
        counters = OpenBenchCounters()
        payload = counters.to_payload()
        assert set(payload.keys()) == {"trinomial", "pentanomial", "crashes", "timelosses", "illegals"}

    def test_to_payload_key_count(self) -> None:
        counters = OpenBenchCounters()
        assert len(counters.to_payload()) == 5


class TestOpenBenchCountersState:
    def test_to_state_keys(self) -> None:
        counters = OpenBenchCounters()
        state = counters.to_state()
        assert set(state.keys()) == {
            "losses",
            "draws",
            "wins",
            "ll",
            "ld",
            "dd",
            "dw",
            "ww",
            "crashes",
            "timelosses",
            "illegals",
        }

    def test_to_state_key_count(self) -> None:
        counters = OpenBenchCounters()
        assert len(counters.to_state()) == 11

    def test_roundtrip(self) -> None:
        """to_state → from_state で同値性が保たれることを検証。"""
        original = OpenBenchCounters(wins=5, draws=3, losses=2, ww=1, dw=2, dd=1, ld=1, ll=0)
        state = original.to_state()
        restored = OpenBenchCounters.from_state(state)
        assert restored.wins == original.wins
        assert restored.draws == original.draws
        assert restored.losses == original.losses
        assert restored.ww == original.ww


# ---------------------------------------------------------------------------
# EngineIoTailEntry
# ---------------------------------------------------------------------------


class TestEngineIoTailEntry:
    def test_required_keys(self) -> None:
        assert _required_keys(EngineIoTailEntry) == {"dir", "line", "ts"}

    def test_optional_keys(self) -> None:
        assert _optional_keys(EngineIoTailEntry) == {"state"}

    def test_key_count(self) -> None:
        total = len(_required_keys(EngineIoTailEntry)) + len(_optional_keys(EngineIoTailEntry))
        assert total == 4


# ---------------------------------------------------------------------------
# GamesSnapshotPayload
# ---------------------------------------------------------------------------


class TestGamesSnapshotPayload:
    def test_required_keys(self) -> None:
        assert _required_keys(GamesSnapshotPayload) == {"type", "rows", "snapshotMeta"}

    def test_no_optional_keys(self) -> None:
        assert _optional_keys(GamesSnapshotPayload) == set()

    def test_key_count(self) -> None:
        assert len(_required_keys(GamesSnapshotPayload)) == 3
