import cshogi

from shogiarena.records.parser.kif import endgame_to_lines


def test_endgame_handles_error_result_code_3():
    board = cshogi.Board()
    # No moves played; simulate end with ERROR (3)
    lastmove_line, reason_line = endgame_to_lines(board=board, result_code=3, handicap="平手", sec=0, sec_sum=0)

    assert isinstance(lastmove_line, str)
    assert isinstance(reason_line, str)
    # エラー(3)は中断扱い
    assert "中断" in lastmove_line
    assert "中断" in reason_line
