"""Tools for validating USI engine protocol compliance."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass

from shogiarena.arena.engines.usi_engine import AsyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ComplianceCheckResult:
    """Result of a single compliance check."""

    name: str
    passed: bool
    detail: str | None = None
    duration: float | None = None


@dataclass(slots=True)
class UsiComplianceReport:
    """Aggregate result of a compliance run."""

    engine_name: str
    engine_id: dict[str, str]
    option_count: int
    checks: tuple[ComplianceCheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass(slots=True)
class UsiComplianceSettings:
    """Configurable parameters for compliance runs."""

    movetime_ms: int = 1000
    handshake_timeout: float = 10.0
    ready_timeout: float = 5.0
    go_timeout: float = 30.0
    stop_timeout: float = 10.0
    stop_delay: float = 0.5
    position: str = "startpos"
    moves: tuple[str, ...] = ()


class UsiComplianceTester:
    """Orchestrate a suite of compliance checks against an engine."""

    def __init__(self, engine: AsyncUsiEngine, settings: UsiComplianceSettings | None = None) -> None:
        self._engine = engine
        self._settings = settings or UsiComplianceSettings()
        self._started = False
        self._engine_id: dict[str, str] = {}
        self._option_count = 0

    async def run(self) -> UsiComplianceReport:
        """Execute the compliance suite and return a structured report."""
        results: list[ComplianceCheckResult] = []
        try:
            handshake = await self._check_handshake()
            results.append(handshake)
            if not handshake.passed:
                return self._build_report(results)

            ready = await self._check_ready()
            results.append(ready)
            if not ready.passed:
                return self._build_report(results)

            new_game = await self._check_new_game()
            results.append(new_game)
            if not new_game.passed:
                return self._build_report(results)

            go = await self._check_go_movetime()
            results.append(go)

            stop = await self._check_stop_with_infinite()
            results.append(stop)

            return self._build_report(results)
        finally:
            await self._safe_close()

    def _build_report(self, results: Iterable[ComplianceCheckResult]) -> UsiComplianceReport:
        checks = tuple(results)
        return UsiComplianceReport(
            engine_name=self._engine.config.name,
            engine_id=dict(self._engine_id),
            option_count=self._option_count,
            checks=checks,
        )

    async def _check_handshake(self) -> ComplianceCheckResult:
        start = time.perf_counter()
        try:
            await self._engine.start()
            self._started = True
            self._engine_id = dict(self._engine.engine_info)
            self._option_count = len(self._engine.get_usi_options())
            if "name" not in self._engine_id:
                return self._failure(
                    "handshake",
                    "Engine did not emit mandatory 'id name' line before usiok",
                    start,
                )
            author = self._engine_id.get("author")
            detail_parts = [f"id name={self._engine_id['name']}"]
            if author:
                detail_parts.append(f"author={author}")
            detail_parts.append(f"options={self._option_count}")
            detail = ", ".join(detail_parts)
            return self._success("handshake", detail, start)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ValueError, OSError, asyncio.TimeoutError) as exc:
            LOGGER.debug("Handshake check failed", exc_info=exc)
            return self._failure("handshake", self._format_exception(exc), start)

    async def _check_ready(self) -> ComplianceCheckResult:
        start = time.perf_counter()
        try:
            await self._engine.trigger_isready(timeout=self._settings.ready_timeout)
            duration = time.perf_counter() - start
            detail = f"readyok in {duration * 1000:.1f} ms"
            return ComplianceCheckResult(name="isready", passed=True, detail=detail, duration=duration)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ValueError, OSError, asyncio.TimeoutError) as exc:
            LOGGER.debug("isready check failed", exc_info=exc)
            return self._failure("isready", self._format_exception(exc), start)

    async def _check_new_game(self) -> ComplianceCheckResult:
        start = time.perf_counter()
        try:
            await self._engine.new_game()
            duration = time.perf_counter() - start
            detail = f"usinewgame + readyok in {duration * 1000:.1f} ms"
            return ComplianceCheckResult(name="usinewgame", passed=True, detail=detail, duration=duration)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ValueError, OSError, asyncio.TimeoutError) as exc:
            LOGGER.debug("usinewgame check failed", exc_info=exc)
            return self._failure("usinewgame", self._format_exception(exc), start)

    async def _check_go_movetime(self) -> ComplianceCheckResult:
        start = time.perf_counter()
        try:
            request = UsiThinkRequest(movetime=self._settings.movetime_ms)
            result = await self._engine.think(
                sfen=self._settings.position,
                moves=self._settings.moves,
                request=request,
                timeout=self._settings.go_timeout,
            )
            duration = time.perf_counter() - start
            bestmove = result.bestmove.to_usi() if result.bestmove is not None else "(none)"
            if result.bestmove is None:
                return self._failure(
                    "go movetime",
                    "Engine returned empty bestmove response",
                    start,
                )
            ponder = f", ponder={result.ponder.to_usi()}" if result.ponder is not None else ""
            detail = f"bestmove={bestmove}{ponder} in {duration * 1000:.1f} ms"
            return ComplianceCheckResult(name="go movetime", passed=True, detail=detail, duration=duration)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ValueError, OSError, asyncio.TimeoutError) as exc:
            LOGGER.debug("go movetime check failed", exc_info=exc)
            return self._failure("go movetime", self._format_exception(exc), start)

    async def _check_stop_with_infinite(self) -> ComplianceCheckResult:
        start = time.perf_counter()
        try:
            await self._engine.new_game()
            request = UsiThinkRequest(infinite=True)
            think_task = asyncio.create_task(
                self._engine.think(
                    sfen=self._settings.position,
                    moves=self._settings.moves,
                    request=request,
                    timeout=self._settings.stop_timeout + self._settings.stop_delay + 1.0,
                )
            )
            search_started = await self._wait_for_search_start(self._settings.stop_timeout)
            if not search_started:
                think_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await think_task
                return self._failure("go infinite/stop", "Engine did not start searching after go infinite", start)

            await asyncio.sleep(self._settings.stop_delay)
            stop_result = await self._engine.stop(timeout=self._settings.stop_timeout)

            try:
                await asyncio.wait_for(think_task, timeout=self._settings.stop_timeout)
            except asyncio.TimeoutError:
                think_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.gather(think_task, return_exceptions=True)
                return self._failure("go infinite/stop", "Engine did not report bestmove after stop", start)
            except asyncio.CancelledError:
                think_task.cancel()
                raise
            if stop_result is None:
                return self._failure("go infinite/stop", "Engine did not return bestmove after stop", start)
            duration = time.perf_counter() - start
            bestmove = stop_result.bestmove.to_usi() if stop_result.bestmove is not None else "(none)"
            detail = f"bestmove={bestmove} after stop in {duration * 1000:.1f} ms"
            return ComplianceCheckResult(name="go infinite/stop", passed=True, detail=detail, duration=duration)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ValueError, OSError, asyncio.TimeoutError) as exc:
            LOGGER.debug("go infinite/stop check failed", exc_info=exc)
            return self._failure("go infinite/stop", self._format_exception(exc), start)

    async def _safe_close(self) -> None:
        if not self._started:
            return
        try:
            await self._engine.close()
        except asyncio.CancelledError:
            raise
        except (RuntimeError, OSError, asyncio.TimeoutError) as exc:
            LOGGER.debug("Error while closing engine after compliance run: %s", exc, exc_info=exc)

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        message = str(exc)
        if not message:
            return exc.__class__.__name__
        return f"{exc.__class__.__name__}: {message}"

    def _success(self, name: str, detail: str, start: float) -> ComplianceCheckResult:
        duration = time.perf_counter() - start
        return ComplianceCheckResult(name=name, passed=True, detail=detail, duration=duration)

    def _failure(self, name: str, detail: str, start: float) -> ComplianceCheckResult:
        duration = time.perf_counter() - start
        return ComplianceCheckResult(name=name, passed=False, detail=detail, duration=duration)

    async def _wait_for_search_start(self, timeout: float) -> bool:
        deadline = time.perf_counter() + max(timeout, 0.0)
        while time.perf_counter() < deadline:
            future = getattr(self._engine, "_bestmove_future", None)
            if future is not None:
                return True
            await asyncio.sleep(0.01)
        return getattr(self._engine, "_bestmove_future", None) is not None


__all__ = [
    "ComplianceCheckResult",
    "UsiComplianceReport",
    "UsiComplianceSettings",
    "UsiComplianceTester",
]
