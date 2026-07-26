"""Dispatch 制御フローの signal（task 0052）。

停止要求で「その局を実行しなかった」ことは、統計を汚す異常ではなく正常な停止手順の一部である。
一般の例外で表すと、SPRT / OpenBench の fail-fast がこれを掴んで run 全体を失敗させてしまう。

shared kernel に置くのは、raise する側（game_session の resource control）と
分類する側（tournament の orchestrator）の双方が型を必要とするため。
"""

from __future__ import annotations


class GameDispatchStoppedError(RuntimeError):
    """停止要求により、その局を開始しないまま dispatch を打ち切ったことを表す。

    実行されなかった局なので terminal record を作らない。
    completed 集合にも入らず、run-health artifact では ``not_played`` に計上される。

    SPRT の正常な早期終了はこの経路を通るため、**fail-fast の対象にしてはならない**。
    掴んで `runtime-error` にすると、成功した検定が失敗した run として記録される。
    """


__all__ = ["GameDispatchStoppedError"]
