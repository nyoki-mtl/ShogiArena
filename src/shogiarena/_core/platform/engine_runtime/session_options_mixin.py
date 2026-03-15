"""USI option normalization and application helpers."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from typing import Any

from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiOption
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int


class AsyncUsiEngineOptionsMixin:
    config: UsiEngineConfig
    _is_started: bool
    _handshake_timeout: float
    _options: dict[str, UsiOption]
    name: str

    start: Any
    _send_command: Any
    _maybe_log_handshake_command: Any

    async def _apply_config_options(self) -> None:
        if not self.config.options:
            return
        for name, value in self.config.options.items():
            await self._set_option(name, value)

    async def apply_engine_options(self, options: Mapping[str, JsonValue] | None) -> None:
        """Apply additional engine options on top of the static configuration."""
        if not options:
            return
        if not self._is_started:
            await self.start()
        for name, value in options.items():
            await self._set_option(name, value)

    async def _set_option(self, name: str, value: JsonValue | None) -> None:
        candidates = self._normalize_option_candidates(name)
        matched = self._collect_options(candidates)
        if not matched:
            await self._wait_for_option(candidates)
            matched = self._collect_options(candidates)
        if not matched:
            raise RuntimeError(f"Engine '{self.name}' does not expose option '{name}'")
        for option in matched:
            await self._apply_option(option.name, value, option)

    def _collect_options(self, candidates: Sequence[str]) -> list[UsiOption]:
        matched: list[UsiOption] = []
        for candidate in candidates:
            option = self._options.get(candidate)
            if option is None:
                continue
            matched.append(option)
        return matched

    async def _wait_for_option(self, candidates: Sequence[str], timeout: float | None = None) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (timeout if timeout is not None else self._handshake_timeout)
        pending = list(dict.fromkeys(candidates))
        while True:
            if any(candidate in self._options for candidate in pending):
                return
            now = loop.time()
            if now >= deadline:
                break
            await asyncio.sleep(0.01)
        missing = ", ".join(candidate for candidate in pending if candidate not in self._options)
        raise RuntimeError(f"Engine '{self.name}' did not expose options: {missing}")

    @staticmethod
    def _normalize_option_candidates(name: str) -> list[str]:
        tokens = [part.strip() for part in re.split(r"[|,]", name) if part.strip()]
        if not tokens:
            return [name]
        # Preserve order but deduplicate
        seen: set[str] = set()
        ordered: list[str] = []
        for token in tokens:
            if token not in seen:
                seen.add(token)
                ordered.append(token)
        return ordered

    @staticmethod
    def _coerce_check_value(value: JsonValue | None) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int) and value in (0, 1):
            return "true" if value == 1 else "false"
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "on", "1"}:
                return "true"
            if normalized in {"false", "no", "off", "0"}:
                return "false"
        raise TypeError(f"check option expects boolean-compatible value; got {value!r}")

    @staticmethod
    def _coerce_spin_value(value: JsonValue | None, option: UsiOption) -> str:
        int_value = coerce_int(value)
        if int_value is None:
            raise TypeError(f"spin option expects integer value; got {value!r}")
        if option.minimum is not None and int_value < option.minimum:
            raise ValueError(f"spin option value {int_value} is below minimum {option.minimum}")
        if option.maximum is not None and int_value > option.maximum:
            raise ValueError(f"spin option value {int_value} exceeds maximum {option.maximum}")
        return str(int_value)

    @staticmethod
    def _coerce_combo_value(value: JsonValue | None, option: UsiOption) -> str:
        text = str(value)
        if option.choices and text not in option.choices:
            raise ValueError(f"combo option value '{text}' must be one of {list(option.choices)}")
        return text

    def _normalize_option_value(self, name: str, value: JsonValue | None, option: UsiOption) -> str | None:
        opt_type = option.option_type
        if opt_type == "button":
            if value not in (None, "", False):
                raise ValueError(f"button option '{name}' must not include a value")
            return None
        if opt_type == "check":
            return self._coerce_check_value(value)
        if opt_type == "spin":
            return self._coerce_spin_value(value, option)
        if opt_type == "combo":
            return self._coerce_combo_value(value, option)
        if opt_type == "string":
            return "" if value is None else str(value)
        return "" if value is None else str(value)

    async def _apply_option(self, name: str, value: JsonValue | None, option: UsiOption) -> None:
        cmd_value = self._normalize_option_value(name, value, option)
        command = f"setoption name {name}"
        if cmd_value is not None and cmd_value != "":
            command += f" value {cmd_value}"
        self._maybe_log_handshake_command(command)
        await self._send_command(command)
        if cmd_value is not None:
            option.current = cmd_value
