"""
Sequential Probability Ratio Test (SPRT) service for arena async system.

Provides the GSPRT trinomial (per-game L/D/W) and pentanomial (paired) models used for automatic
statistical stopping. The model is part of the resume test definition (run_artifact_hashes) so a
run cannot be resumed under a different model.
"""

import logging
import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import NotRequired, TypedDict

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.sprt_models import (
    SPRT_MODEL_GSPRT_PENTANOMIAL,
    SPRT_MODEL_GSPRT_TRINOMIAL,
    SUPPORTED_SPRT_MODELS,
)
from shogiarena._core.shared.kernel.statistics.gsprt import compute_llr, sprt_bounds
from shogiarena._core.shared.kernel.statistics.pentanomial_pairing import (
    PENTANOMIAL_BIN_COUNT,
    pair_score_bin_index,
)

logger = logging.getLogger(__name__)

# SPRT model identifiers（正本は shared kernel。resume 側も同じ識別子を使う）。
_SUPPORTED_SPRT_MODELS = SUPPORTED_SPRT_MODELS

# The pentanomial model uses a Brownian/normal approximation, not an exact finite-sample test.
# Keep it fail-closed until there is both a conventional CLT-sized sample and enough observations
# in two outcome classes to estimate non-zero variance without the 1e-3 regularization prior
# dominating the result. This is an approximation-safety floor, not a claim that 30 pairs alone
# calibrate the requested alpha/beta error rates.
PENTANOMIAL_MIN_PAIRS_FOR_LLR = 30
PENTANOMIAL_MIN_VARIANCE_SUPPORT = 5

# Valid per-game tested-perspective scores (loss / draw / win).
_VALID_GAME_SCORES = (0.0, 0.5, 1.0)


def _validate_game_score(score: float) -> float:
    """Reject non-finite or off-grid scores so they cannot silently land in the wrong bin."""
    if score not in _VALID_GAME_SCORES:  # also rejects nan/inf and off-grid values like 0.25
        raise ValueError(f"game score must be one of {_VALID_GAME_SCORES}, got {score!r}")
    return score


def validate_pentanomial_preconditions(*, flip_policy: str, num_engines: int, games_per_pair: int) -> None:
    """Fail-fast if a schedule cannot produce the reversed-colour pairs pentanomial SPRT needs.

    Pentanomial pairing requires colour-reversed game pairs of a single 1v1 matchup, so the
    schedule must use ``pair_both`` flipping, exactly two engines, and an even number of games per
    pair (an odd count would always leave one game unpaired / uncounted).
    """
    problems: list[str] = []
    if flip_policy != "pair_both":
        problems.append(f"rules.initial_positions.flip_policy must be 'pair_both' (got {flip_policy!r})")
    if num_engines != 2:
        problems.append(f"exactly 2 engines are required (got {num_engines})")
    if games_per_pair % 2 != 0:
        problems.append(f"tournament.games_per_pair must be even (got {games_per_pair})")
    if problems:
        raise ValueError(f"{SPRT_MODEL_GSPRT_PENTANOMIAL} requires: " + "; ".join(problems))


class PendingPairHalfSnapshot(TypedDict):
    """One buffered, not-yet-paired game half (pentanomial pause/resume)."""

    sfen: str
    pair_slot: int
    is_tested_black: bool
    score: float


class SprtStateSnapshot(TypedDict):
    """SPRT ステートのスナップショット（pause/resume 用）。"""

    model: str
    elo0: float
    elo1: float
    alpha: float
    beta: float
    wins: int
    draws: int
    losses: int
    games_played: int
    llr: float
    min_pairs: int
    penta_bins: list[int]
    pending: list[PendingPairHalfSnapshot]
    # 停止判定のラッチ（task 0052 / review H1）。古い state.json には無いので optional。
    is_decision_latched: NotRequired[bool]
    late_games: NotRequired[int]


class SprtDecision(Enum):
    """SPRT test decision states."""

    CONTINUE = "continue"
    ACCEPT_H0 = "accept_h0"  # Null hypothesis (no improvement)
    ACCEPT_H1 = "accept_h1"  # Alternative hypothesis (improvement)


@dataclass(kw_only=True)
class SprtResult:
    """SPRT test result information."""

    llr: float  # Log Likelihood Ratio
    lower_bound: float  # Lower bound (H0 acceptance)
    upper_bound: float  # Upper bound (H1 acceptance)
    decision: SprtDecision
    games_played: int
    wins: int
    draws: int
    losses: int
    win_rate: float
    elo_estimate: float | None = None
    pending_pairs: int = 0  # incomplete (sfen, pair_slot) buffers awaiting their partner game
    pending_games: int = 0  # buffered games not yet counted into a pentanomial pair
    # 標本を締めた後に完了した局数（in-flight だった局）。検定へは加えていない。
    late_games: int = 0


class Sprt:
    """
    Sequential Probability Ratio Test implementation.

    Tests whether the true Elo difference is closer to elo0 (H0) or elo1 (H1)
    using Type I and Type II error rates alpha and beta.
    """

    def __init__(
        self,
        elo0: float,
        elo1: float,
        alpha: float = 0.05,
        beta: float = 0.05,
        model: str = SPRT_MODEL_GSPRT_TRINOMIAL,
        min_pairs: int = PENTANOMIAL_MIN_PAIRS_FOR_LLR,
    ) -> None:
        """
        Initialize SPRT test.

        Args:
            elo0: Null hypothesis Elo difference (typically 0)
            elo1: Alternative hypothesis Elo difference (e.g., 5.0)
            alpha: Type I error rate (false positive, rejecting true H0)
            beta: Type II error rate (false negative, accepting false H0)
            model: SPRT statistical model identifier.
            min_pairs: Minimum completed pairs before a pentanomial decision (>= the hard floor).
        """
        if model not in _SUPPORTED_SPRT_MODELS:
            raise ValueError(f"Unsupported SPRT model: {model!r}")
        if elo1 <= elo0:
            raise ValueError(f"elo1 ({elo1}) must be greater than elo0 ({elo0})")

        self.model = model
        self._is_pentanomial = model == SPRT_MODEL_GSPRT_PENTANOMIAL
        self.elo0 = elo0
        self.elo1 = elo1
        self.alpha = alpha
        self.beta = beta
        self._min_pairs = max(PENTANOMIAL_MIN_PAIRS_FOR_LLR, min_pairs)

        # Wald bounds (also validates alpha/beta and alpha + beta < 1).
        self.lower_bound, self.upper_bound = sprt_bounds(alpha, beta)

        # Per-game counters (drive win_rate / elo estimate / the trinomial histogram).
        self.wins = 0
        self.draws = 0
        self.losses = 0
        self.games_played = 0
        self.llr = 0.0

        # Pentanomial state: completed-pair bins (score-ascending) and a buffer of game halves
        # keyed by (sfen, pair_slot) awaiting their reversed-colour partner.
        self._penta_bins = [0] * PENTANOMIAL_BIN_COUNT
        self._pending: dict[tuple[str, int], dict[str, list[float]]] = {}

        # 停止判定のラッチ（task 0052 / review H1）。停止規則は「停止した時点の標本」の
        # 関数でなければならない。ラッチ後に到着した in-flight 局を標本へ足すと、
        # 一度確定した decision が continue へ戻り、run が成功したのか未完了なのかが
        # 事後に変わってしまう。到着数だけ ``_late_games`` に数えて捨てる。
        self._decision_latch: SprtResult | None = None
        self._late_games = 0

        logger.debug(f"SPRT initialized: model={model}, H0={elo0}, H1={elo1}, alpha={alpha}, beta={beta}")
        logger.debug(f"SPRT bounds: lower={self.lower_bound:.4f}, upper={self.upper_bound:.4f}")

    # --- LLR ------------------------------------------------------------------
    def _recompute_llr(self) -> None:
        """Recompute the GSPRT log-likelihood ratio for the active model."""
        if self._is_pentanomial:
            completed_pairs = sum(self._penta_bins)
            if completed_pairs < self._min_pairs:
                self.llr = 0.0
                return
            populated_counts = sorted((count for count in self._penta_bins if count > 0), reverse=True)
            if len(populated_counts) < 2 or populated_counts[1] < PENTANOMIAL_MIN_VARIANCE_SUPPORT:
                # A single outcome class has zero empirical variance. Requiring at least five
                # observations in a second class prevents the numerical regularizer (rather than
                # observed data) from manufacturing a tiny-sample decision.
                self.llr = 0.0
                return
            self.llr = compute_llr(self._penta_bins, elo0=self.elo0, elo1=self.elo1)
            return
        bins = [self.losses, self.draws, self.wins]
        if any(count == 0 for count in bins):
            # OpenBench-style trinomial guard: do not produce a decision until every outcome
            # (loss, draw, win) has occurred at least once. On a degenerate one-sided sample the
            # regularized variance is artificially tiny, so without this the test would stop after
            # a handful of games (e.g. 2-0), far below the nominal alpha/beta error rates.
            self.llr = 0.0
            return
        self.llr = compute_llr(bins, elo0=self.elo0, elo1=self.elo1)

    # --- Counters / buffer ----------------------------------------------------
    def _count_game(self, score: float) -> None:
        self.games_played += 1
        if score >= 1.0:
            self.wins += 1
        elif score <= 0.0:
            self.losses += 1
        else:
            self.draws += 1

    def _pending_games(self) -> int:
        return sum(len(slot["black"]) + len(slot["white"]) for slot in self._pending.values())

    def _record_pair(self, black_score: float, white_score: float) -> None:
        self._penta_bins[pair_score_bin_index(black_score + white_score)] += 1

    # --- Result construction --------------------------------------------------
    def _build_result(self) -> SprtResult:
        """現在の status。ラッチ済みなら締めた時点の標本を返す。"""
        if self._decision_latch is not None:
            return replace(self._decision_latch, late_games=self._late_games)
        return self._build_live_result()

    def _build_live_result(self) -> SprtResult:
        decision = self._make_decision()
        win_rate = (self.wins + 0.5 * self.draws) / max(1, self.games_played)
        elo_estimate = self._win_rate_to_elo(win_rate) if self.games_played > 0 else None
        return SprtResult(
            llr=self.llr,
            lower_bound=self.lower_bound,
            upper_bound=self.upper_bound,
            decision=decision,
            games_played=self.games_played,
            wins=self.wins,
            draws=self.draws,
            losses=self.losses,
            win_rate=win_rate,
            elo_estimate=elo_estimate,
            pending_pairs=len(self._pending),
            pending_games=self._pending_games(),
            late_games=self._late_games,
        )

    # --- Decision latch -------------------------------------------------------
    @property
    def is_decision_latched(self) -> bool:
        """停止判定を確定させ、標本を締めたか。"""
        return self._decision_latch is not None

    @property
    def late_games(self) -> int:
        """標本を締めた後に完了した局数。DB には残るが検定へは加えていない。"""
        return self._late_games

    def latch_decision(self) -> SprtResult:
        """現在の標本で停止判定を確定させる（冪等）。

        呼ぶのは「この decision を理由に run を止める」と決めた側だけにする。
        ``is_finished()`` が True でも ``min_games`` に届いていない間は run が続くので、
        そこでラッチすると以降の局をすべて捨ててしまう。
        """
        if self._decision_latch is None:
            self._decision_latch = self._build_live_result()
            logger.info(
                "SPRT sample closed at games=%d (LLR=%.4f, decision=%s); "
                "later in-flight games are recorded but excluded from the test",
                self._decision_latch.games_played,
                self._decision_latch.llr,
                self._decision_latch.decision.value,
            )
        return self._build_result()

    def _count_late_game(self, games: int = 1) -> SprtResult:
        """ラッチ後に到着した局を、標本へ加えずに数えるだけにする。"""
        self._late_games += games
        return self._build_result()

    # --- Snapshot -------------------------------------------------------------
    def to_snapshot(self) -> SprtStateSnapshot:
        """Serialize SPRT state for pause/resume."""
        pending: list[PendingPairHalfSnapshot] = []
        for (sfen, pair_slot), slot in self._pending.items():
            for score in slot["black"]:
                pending.append({"sfen": sfen, "pair_slot": pair_slot, "is_tested_black": True, "score": score})
            for score in slot["white"]:
                pending.append({"sfen": sfen, "pair_slot": pair_slot, "is_tested_black": False, "score": score})
        return {
            "model": self.model,
            "elo0": self.elo0,
            "elo1": self.elo1,
            "alpha": self.alpha,
            "beta": self.beta,
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.losses,
            "games_played": self.games_played,
            "llr": self.llr,
            "min_pairs": self._min_pairs,
            "penta_bins": list(self._penta_bins),
            "pending": pending,
            "is_decision_latched": self._decision_latch is not None,
            "late_games": self._late_games,
        }

    @classmethod
    def from_snapshot(cls, snapshot: SprtStateSnapshot) -> "Sprt":
        """Restore SPRT state from a snapshot."""
        sprt = cls(
            elo0=float(snapshot["elo0"]),
            elo1=float(snapshot["elo1"]),
            alpha=float(snapshot["alpha"]),
            beta=float(snapshot["beta"]),
            model=str(snapshot["model"]),
            min_pairs=int(snapshot["min_pairs"]),
        )
        sprt.wins = int(snapshot["wins"])
        sprt.draws = int(snapshot["draws"])
        sprt.losses = int(snapshot["losses"])
        sprt.games_played = int(snapshot["games_played"])

        bins = snapshot["penta_bins"]
        if len(bins) != PENTANOMIAL_BIN_COUNT:
            raise ValueError(f"penta_bins must have {PENTANOMIAL_BIN_COUNT} entries, got {len(bins)}")
        sprt._penta_bins = [int(count) for count in bins]
        for half in snapshot["pending"]:
            key = (str(half["sfen"]), int(half["pair_slot"]))
            slot = sprt._pending.setdefault(key, {"black": [], "white": []})
            slot["black" if half["is_tested_black"] else "white"].append(float(half["score"]))

        # Recompute the LLR from the restored counts rather than trusting the stored value, so a
        # snapshot can never carry an LLR computed under a different model.
        sprt._recompute_llr()

        # ラッチは復元する。counter だけから導出しないのは、``min_games`` に届く前に
        # 一時的に bound を越えた状態と、実際に停止した状態を区別できないため
        # （前者でラッチすると以降の局をすべて捨ててしまう）。
        sprt._late_games = int(snapshot.get("late_games", 0))
        if snapshot.get("is_decision_latched", False):
            sprt.latch_decision()
        return sprt

    # --- Ingestion: trinomial -------------------------------------------------
    def add_game_result(self, result: GameResult) -> SprtResult:
        """Add a single game result (trinomial model). ``result`` is from the tested perspective."""
        if self._is_pentanomial:
            raise ValueError("add_game_result requires the trinomial model; use add_game_observation")
        if self._decision_latch is not None:
            return self._count_late_game()
        # Strict input contract: only decisive results and genuine draws are valid SPRT
        # observations. Callers must normalize/skip non-game outcomes (ERROR/INVALID/PAUSED)
        # before reaching here; silently folding them into draws distorts the test.
        if result == GameResult.WHITE_WIN:  # tested engine win (normalized by the caller)
            self.games_played += 1
            self.wins += 1
        elif result == GameResult.BLACK_WIN:
            self.games_played += 1
            self.losses += 1
        elif result.is_draw():
            self.games_played += 1
            self.draws += 1
        else:
            raise ValueError(f"add_game_result requires a decisive or draw result, got {result!r}")
        self._recompute_llr()
        result_obj = self._build_result()
        logger.debug(
            f"SPRT updated: games={self.games_played}, LLR={self.llr:.4f}, decision={result_obj.decision.value}"
        )
        return result_obj

    # --- Ingestion: pentanomial -----------------------------------------------
    def add_game_observation(
        self, *, sfen: str, pair_slot: int, is_tested_black: bool, tested_score: float
    ) -> SprtResult:
        """Buffer a single game (pentanomial model) and emit a pair when both colours arrive.

        ``tested_score`` is the per-game score from the tested engine's perspective (0.0/0.5/1.0).
        """
        if not self._is_pentanomial:
            raise ValueError("add_game_observation requires the pentanomial model")
        if self._decision_latch is not None:
            return self._count_late_game()
        _validate_game_score(tested_score)
        self._count_game(tested_score)
        key = (sfen, pair_slot)
        slot = self._pending.setdefault(key, {"black": [], "white": []})
        slot["black" if is_tested_black else "white"].append(tested_score)
        while slot["black"] and slot["white"]:
            self._record_pair(slot["black"].pop(0), slot["white"].pop(0))
        if not slot["black"] and not slot["white"]:
            del self._pending[key]
        self._recompute_llr()
        return self._build_result()

    def add_paired_observation(self, *, black_score: float, white_score: float) -> SprtResult:
        """Add a complete pair (pentanomial model) where the tested engine played both colours.

        Used by callers that already hold both games of a pair (e.g. SPSA LTC), so no buffering is
        needed. ``black_score``/``white_score`` are tested-perspective per-game scores.
        """
        if not self._is_pentanomial:
            raise ValueError("add_paired_observation requires the pentanomial model")
        if self._decision_latch is not None:
            return self._count_late_game(2)
        _validate_game_score(black_score)
        _validate_game_score(white_score)
        self._count_game(black_score)
        self._count_game(white_score)
        self._record_pair(black_score, white_score)
        self._recompute_llr()
        return self._build_result()

    # --- Decision / status ----------------------------------------------------
    def _make_decision(self) -> SprtDecision:
        """Make SPRT decision based on current LLR and bounds."""
        if self.llr >= self.upper_bound:
            return SprtDecision.ACCEPT_H1
        elif self.llr <= self.lower_bound:
            return SprtDecision.ACCEPT_H0
        else:
            return SprtDecision.CONTINUE

    def _win_rate_to_elo(self, win_rate: float) -> float:
        """Convert a draw-aware win rate (0..1) to an Elo difference estimate."""
        # Clamp win rate to avoid log(0)
        win_rate = max(0.001, min(0.999, win_rate))
        return -400.0 * math.log10(1.0 / win_rate - 1.0)

    def get_status(self) -> SprtResult:
        """Get current SPRT status without adding a new result."""
        return self._build_result()

    def reset(self) -> None:
        """Reset SPRT state for a new test."""
        self.wins = 0
        self.draws = 0
        self.losses = 0
        self.games_played = 0
        self.llr = 0.0
        self._penta_bins = [0] * PENTANOMIAL_BIN_COUNT
        self._pending.clear()
        self._decision_latch = None
        self._late_games = 0
        logger.debug("SPRT reset")

    def is_finished(self) -> bool:
        """Check if SPRT test has reached a decision."""
        if self._decision_latch is not None:
            return True
        return self._make_decision() != SprtDecision.CONTINUE

    def __str__(self) -> str:
        """String representation for debugging."""
        status = self.get_status()
        elo_est = "n/a" if status.elo_estimate is None else f"{status.elo_estimate:.1f}"
        return (
            f"SPRT(games={status.games_played}, LLR={status.llr:.4f}, "
            f"bounds=[{status.lower_bound:.4f}, {status.upper_bound:.4f}], "
            f"decision={status.decision.value}, elo_est={elo_est})"
        )
