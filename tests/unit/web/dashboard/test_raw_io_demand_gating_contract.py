"""Live card の raw I/O demand gating source contract（task 0052 / review finding M7）。

frontend の pure function は Vitest 側（``rawIoDemandGating.test.ts``）で検証する。
ここでは「card の更新経路そのものが購読を発行しない」という構造を固定する。

Live view の既定表示では、並列数に関係なく raw I/O subscription が 0 でなければならない。
card 更新側から購読を発行すると、その不変条件が全 card 分だけ壊れる。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import shogiarena

_CARDS_DIR = (
    Path(shogiarena.__file__).parent
    / "_core"
    / "interfaces"
    / "dashboard"
    / "frontend"
    / "src"
    / "modules"
    / "live"
    / "components"
    / "cards"
)


def _read(name: str) -> str:
    path = _CARDS_DIR / name
    if not path.exists():  # pragma: no cover - packaging guard
        pytest.skip(f"frontend source is not available: {path}")
    return path.read_text(encoding="utf-8")


def test_card_update_path_never_toggles_engine_log_visibility() -> None:
    """phase 遷移や prep 表示から購読を発行しないこと。"""

    source = _read("card-data.ts")
    assert "setEngineLogVisibility" not in source
    assert "emitEngineLogToggle" not in source
    # ``emit: true`` は購読要求を伴う preference 変更のオプション。
    assert "emit: true" not in source


def test_prep_overlay_is_rendered_from_lifecycle_state_only() -> None:
    """kickoff 検出と prep 表示が ``engine_status`` 由来であること。"""

    source = _read("card-data.ts")
    assert "isPrepOverlayVisibleFor" in source
    assert "engineStatus?.black?.io_tail" in source
    assert "engineStatus?.white?.io_tail" in source


def test_subscriptions_are_emitted_only_from_the_explicit_preference_path() -> None:
    """購読の発行元が、利用者操作に対応する preference 経路に限られること。"""

    source = _read("cards-api.ts")
    # 明示的に開いた role が無ければ、game key 変更で再購読しない。
    assert "if (!black && !white) return;" in source
    # card を閉じるときは開いていた role だけ解除する。
    assert "if (black) emitEngineLogToggle(gid, 'black', false);" in source
    assert "if (white) emitEngineLogToggle(gid, 'white', false);" in source


def test_toggle_reads_the_preference_not_the_display_class() -> None:
    """prep overlay の表示 class を購読 preference として読まないこと。

    読むと、prep 中に生ログを開こうとしても ``nextPref=false`` になり subscribe しない。
    """

    source = _read("cards-api.ts")
    assert "const isOpen = resolveManualOpenRoles(cardState)[role];" in source
    assert "cardEl?.classList.contains(`worker-card--log-visible-${role}`)" not in source


def test_game_completion_releases_the_raw_subscription() -> None:
    """game completion で raw topic を 0 に戻すこと（benchmark.md B4）。

    topic は game 単位なので、終局した対局の購読は解放する。
    表示（利用者の意図）は残し、再購読は新しい game key でだけ起きる。
    """

    cards_api = _read("cards-api.ts")
    assert "const releaseRawSubscriptionsForCard" in cards_api
    assert "releaseRawSubscriptions: releaseRawSubscriptionsForCard," in cards_api

    card_data = _read("card-data.ts")
    assert "releaseRawSubscriptions?.(cardState);" in card_data
    # 解放済みの game を次の更新で再購読しない。
    assert "engineLogReleasedGameKey" in card_data
    assert "!isReleasedGame" in card_data
