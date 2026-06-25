"""USI option normalization and application helpers."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.engine_runtime.usi_engine_session_models import UsiOptionValidationMode
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiOption
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int

logger = logging.getLogger(__name__)


class AsyncUsiEngineOptionsMixin:
    config: UsiEngineConfig
    _is_started: bool
    _handshake_timeout: float
    _options: dict[str, UsiOption]
    name: str

    start: Any
    _send_command: Any
    _maybe_log_handshake_command: Any
    trigger_isready: Any
    _emit_lifecycle_event: Any

    async def _apply_config_options(self) -> None:
        applied_count = 0
        if not self.config.options:
            self._emit_lifecycle_event("options_applied", details={"source": "config", "count": 0})
            return
        for name, value in self.config.options.items():
            await self._set_option(name, value)
            applied_count += 1
        self._emit_lifecycle_event("options_applied", details={"source": "config", "count": applied_count})

    async def apply_engine_options(
        self,
        options: Mapping[str, object] | None,
        *,
        clear_hash: bool = True,
        after_setoption: Literal["none", "isready"] = "isready",
        validation: UsiOptionValidationMode | Mapping[str, UsiOptionValidationMode] | None = None,
    ) -> None:
        """Apply additional engine options on top of the static configuration."""
        if not options:
            return
        if after_setoption not in {"none", "isready"}:
            raise ValueError("after_setoption must be one of: none, isready")
        if not self._is_started:
            await self.start()
        applied_count = 0
        for name, value in options.items():
            await self._set_option(name, value, validation=validation)
            applied_count += 1
        did_clear_hash = False
        if clear_hash:
            did_clear_hash = await self._clear_hash_if_available()
        if after_setoption == "isready":
            await self.trigger_isready()
        self._emit_lifecycle_event(
            "options_applied",
            details={"source": "runtime", "count": applied_count, "clear_hash": did_clear_hash},
        )

    async def _clear_hash_if_available(self) -> bool:
        clear_hash_option = self._options.get("Clear Hash")
        if clear_hash_option is None or clear_hash_option.option_type != "button":
            return False
        await self._apply_option(clear_hash_option.name, None, clear_hash_option, validation_mode="strict")
        return True

    async def _set_option(
        self,
        name: str,
        value: object | None,
        *,
        validation: UsiOptionValidationMode | Mapping[str, UsiOptionValidationMode] | None = None,
    ) -> None:
        validation_mode = self._resolve_option_validation(name, validation=validation)
        if validation_mode == "raw":
            await self._apply_raw_option(name, value)
            return
        candidates = self._normalize_option_candidates(name)
        matched = self._collect_options(candidates)
        if not matched and validation_mode == "warn":
            logger.warning("[%s] applying unknown USI option %r with %s validation", self.name, name, validation_mode)
            await self._apply_raw_option(name, value)
            return
        if not matched:
            await self._wait_for_option(candidates)
            matched = self._collect_options(candidates)
        if not matched:
            raise RuntimeError(f"Engine '{self.name}' does not expose option '{name}'")
        for option in matched:
            option_validation_mode = self._resolve_option_validation(
                option.name,
                validation=validation,
                fallback=validation_mode,
            )
            await self._apply_option(option.name, value, option, validation_mode=option_validation_mode)

    def _resolve_option_validation(
        self,
        name: str,
        *,
        validation: UsiOptionValidationMode | Mapping[str, UsiOptionValidationMode] | None,
        fallback: UsiOptionValidationMode | None = None,
    ) -> UsiOptionValidationMode:
        if isinstance(validation, str):
            if validation == "warn":
                return "warn"
            if validation == "raw":
                return "raw"
            if validation == "allow_unlisted_combo_value":
                return "allow_unlisted_combo_value"
            return "strict"
        if validation is not None:
            mode = validation.get(name)
            if mode is not None:
                return mode
        configured = self.config.option_validation_overrides.get(name)
        if configured is not None:
            return configured
        if fallback is not None:
            return fallback
        return self.config.option_validation_default

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
    def _coerce_check_value(value: object | None) -> str:
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
    def _coerce_spin_value(value: object | None, option: UsiOption) -> str:
        int_value = coerce_int(value)
        if int_value is None:
            raise TypeError(f"spin option expects integer value; got {value!r}")
        if option.minimum is not None and int_value < option.minimum:
            raise ValueError(f"spin option value {int_value} is below minimum {option.minimum}")
        if option.maximum is not None and int_value > option.maximum:
            raise ValueError(f"spin option value {int_value} exceeds maximum {option.maximum}")
        return str(int_value)

    @staticmethod
    def _coerce_combo_value(value: object | None, option: UsiOption, *, allow_unlisted: bool = False) -> str:
        text = str(value)
        if option.choices and text not in option.choices and not allow_unlisted:
            raise ValueError(f"combo option value '{text}' must be one of {list(option.choices)}")
        return text

    def _normalize_option_value(
        self,
        name: str,
        value: object | None,
        option: UsiOption,
        *,
        validation_mode: UsiOptionValidationMode,
    ) -> str | None:
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
            return self._coerce_combo_value(
                value,
                option,
                allow_unlisted=validation_mode == "allow_unlisted_combo_value",
            )
        if opt_type == "string":
            return "" if value is None else str(value)
        return "" if value is None else str(value)

    async def _apply_option(
        self,
        name: str,
        value: object | None,
        option: UsiOption,
        *,
        validation_mode: UsiOptionValidationMode,
    ) -> None:
        if validation_mode == "raw":
            await self._apply_raw_option(name, value)
            if value is not None:
                option.current = str(value)
            return
        try:
            cmd_value = self._normalize_option_value(name, value, option, validation_mode=validation_mode)
        except (TypeError, ValueError) as exc:
            if validation_mode != "warn":
                raise
            logger.warning(
                "[%s] applying USI option %r despite validation warning: %s",
                self.name,
                name,
                exc,
            )
            await self._apply_raw_option(name, value)
            if value is not None:
                option.current = str(value)
            return
        command = self._format_setoption_command(name, cmd_value)
        self._maybe_log_handshake_command(command)
        await self._send_command(command)
        if cmd_value is not None:
            option.current = cmd_value

    async def _apply_raw_option(self, name: str, value: object | None) -> None:
        command = self._format_setoption_command(name, None if value is None else str(value))
        self._maybe_log_handshake_command(command)
        await self._send_command(command)

    @staticmethod
    def _format_setoption_command(name: str, value: str | None) -> str:
        command = f"setoption name {name}"
        if value is not None and value != "":
            command += f" value {value}"
        return command
