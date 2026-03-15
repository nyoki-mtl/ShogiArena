from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec


def _create_spec(round_num: int) -> str:
    spec = GameSpec.create(
        black="EngineAlpha",
        white="EngineBeta",
        sfen="startpos",
        round_num=round_num,
        seed="test-seed",
    )
    return spec.game_id


def test_game_id_is_one_based_zero_padded() -> None:
    first_game_id = _create_spec(0)
    second_game_id = _create_spec(1)

    assert first_game_id.startswith("g0001-")
    assert second_game_id.startswith("g0002-")


def test_game_id_rolls_over_decimally() -> None:
    tenth_game_id = _create_spec(9)
    eleventh_game_id = _create_spec(10)

    assert tenth_game_id.startswith("g0010-")
    assert eleventh_game_id.startswith("g0011-")
