"""Shared HTTP helpers for the OpenBench client."""

from __future__ import annotations

from collections.abc import Mapping

import aiohttp
from pydantic import BaseModel, ConfigDict, ValidationError

from shogiarena._core.shared.kernel.json_coercion import (
    coerce_json_object_serialized as _coerce_json_object_serialized,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

from .client_types import OpenBenchClientConfig, OpenBenchError


class _OpenBenchResponseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class OpenBenchClientHttpMixin:
    _http: aiohttp.ClientSession | None
    _config: OpenBenchClientConfig

    def _auth_payload(self) -> dict[str, str]:
        return {
            "username": self._config.username,
            "password": self._config.password,
        }

    def _url(self, endpoint: str) -> str:
        return f"{self._config.server.rstrip('/')}/{endpoint.lstrip('/')}"

    async def _post(self, endpoint: str, payload: Mapping[str, JsonValue]) -> JsonObject:
        if self._http is None:
            raise OpenBenchError("OpenBench HTTP session is not initialized")
        url = self._url(endpoint)
        try:
            async with self._http.post(url, data=payload) as response:
                response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientResponseError as exc:
            raise OpenBenchError(f"OpenBench request failed [{exc.status}] {endpoint}") from exc
        except aiohttp.ContentTypeError as exc:
            raise OpenBenchError(f"OpenBench response was not JSON: {endpoint}") from exc
        except aiohttp.ClientError as exc:
            raise OpenBenchError(f"OpenBench network error at {endpoint}: {exc}") from exc
        if not isinstance(data, dict):
            raise OpenBenchError(f"OpenBench endpoint returned non-object payload: {endpoint}")
        try:
            parsed = _OpenBenchResponseModel.model_validate(data)
        except ValidationError as exc:
            raise OpenBenchError(f"OpenBench endpoint returned invalid payload: {endpoint}: {exc}") from exc
        return _coerce_json_object_serialized(
            parsed.model_dump(mode="python"),
            field_name=f"openbench response ({endpoint})",
        )

    async def _get(self, endpoint: str) -> JsonObject:
        if self._http is None:
            raise OpenBenchError("OpenBench HTTP session is not initialized")
        url = self._url(endpoint)
        try:
            async with self._http.get(url) as response:
                response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientResponseError as exc:
            raise OpenBenchError(f"OpenBench request failed [{exc.status}] {endpoint}") from exc
        except aiohttp.ContentTypeError as exc:
            raise OpenBenchError(f"OpenBench response was not JSON: {endpoint}") from exc
        except aiohttp.ClientError as exc:
            raise OpenBenchError(f"OpenBench network error at {endpoint}: {exc}") from exc
        if not isinstance(data, dict):
            raise OpenBenchError(f"OpenBench endpoint returned non-object payload: {endpoint}")
        try:
            parsed = _OpenBenchResponseModel.model_validate(data)
        except ValidationError as exc:
            raise OpenBenchError(f"OpenBench endpoint returned invalid payload: {endpoint}: {exc}") from exc
        return _coerce_json_object_serialized(
            parsed.model_dump(mode="python"),
            field_name=f"openbench response ({endpoint})",
        )

    async def _post_text(
        self,
        endpoint: str,
        payload: Mapping[str, JsonValue],
        *,
        should_allow_redirects: bool = True,
    ) -> tuple[int, str, str]:
        if self._http is None:
            raise OpenBenchError("OpenBench HTTP session is not initialized")
        url = self._url(endpoint)
        try:
            async with self._http.post(url, data=payload, allow_redirects=should_allow_redirects) as response:
                response.raise_for_status()
                data = await response.text()
                final_url = str(response.url)
                return response.status, final_url, data
        except aiohttp.ClientResponseError as exc:
            raise OpenBenchError(f"OpenBench request failed [{exc.status}] {endpoint}") from exc
        except aiohttp.ClientError as exc:
            raise OpenBenchError(f"OpenBench network error at {endpoint}: {exc}") from exc
