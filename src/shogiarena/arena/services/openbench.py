from __future__ import annotations

import asyncio
import json
import logging
import platform
import re
import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypedDict, cast
from urllib.parse import urlparse

import aiohttp
import psutil

from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.utils.cpuinfo import get_cpu_info
from shogiarena.utils.types.coerce import coerce_int
from shogiarena.utils.types.types import GameRecordPlayersDict, GameResult

logger = logging.getLogger(__name__)


class _CountersPayload(TypedDict):
    """OpenBench サーバ送信用カウンタペイロード。"""

    trinomial: str
    pentanomial: str
    crashes: int
    timelosses: int
    illegals: int


class _CountersState(TypedDict):
    """OpenBench カウンタの永続化用ステート。"""

    losses: int
    draws: int
    wins: int
    ll: int
    ld: int
    dd: int
    dw: int
    ww: int
    crashes: int
    timelosses: int
    illegals: int


class _AuthPayload(TypedDict):
    """OpenBench 認証ペイロード。"""

    username: str
    password: str


class _ClientStateSnapshot(TypedDict):
    """OpenBench クライアントのステートスナップショット。"""

    submitted: _CountersState
    last_synced_games: int
    target_test_id: int | None
    claimed_test_id: int | None
    result_id: int | None
    blacklist: list[int]


_GAME_RESULT_ERROR = GameResult.from_str("ERROR")

_RECOVERABLE_WORKER_ERRORS = frozenset(
    {
        "Invalid Secret Token",
        "Bad Machine Id",
        "Server Configuration Changed",
    }
)


class OpenBenchError(RuntimeError):
    pass


@dataclass(slots=True)
class OpenBenchCounters:
    losses: int = 0
    draws: int = 0
    wins: int = 0
    ll: int = 0
    ld: int = 0
    dd: int = 0
    dw: int = 0
    ww: int = 0
    crashes: int = 0
    timelosses: int = 0
    illegals: int = 0

    @property
    def games(self) -> int:
        return self.losses + self.draws + self.wins

    def to_payload(self) -> _CountersPayload:
        return {
            "trinomial": f"{self.losses} {self.draws} {self.wins}",
            "pentanomial": f"{self.ll} {self.ld} {self.dd} {self.dw} {self.ww}",
            "crashes": self.crashes,
            "timelosses": self.timelosses,
            "illegals": self.illegals,
        }

    def to_state(self) -> _CountersState:
        return {
            "losses": self.losses,
            "draws": self.draws,
            "wins": self.wins,
            "ll": self.ll,
            "ld": self.ld,
            "dd": self.dd,
            "dw": self.dw,
            "ww": self.ww,
            "crashes": self.crashes,
            "timelosses": self.timelosses,
            "illegals": self.illegals,
        }

    @classmethod
    def from_state(cls, data: dict[str, object]) -> OpenBenchCounters:
        return cls(
            losses=coerce_int(data.get("losses")) or 0,
            draws=coerce_int(data.get("draws")) or 0,
            wins=coerce_int(data.get("wins")) or 0,
            ll=coerce_int(data.get("ll")) or 0,
            ld=coerce_int(data.get("ld")) or 0,
            dd=coerce_int(data.get("dd")) or 0,
            dw=coerce_int(data.get("dw")) or 0,
            ww=coerce_int(data.get("ww")) or 0,
            crashes=coerce_int(data.get("crashes")) or 0,
            timelosses=coerce_int(data.get("timelosses")) or 0,
            illegals=coerce_int(data.get("illegals")) or 0,
        )

    def delta_from(self, base: OpenBenchCounters) -> OpenBenchCounters:
        return OpenBenchCounters(
            losses=max(0, self.losses - base.losses),
            draws=max(0, self.draws - base.draws),
            wins=max(0, self.wins - base.wins),
            ll=max(0, self.ll - base.ll),
            ld=max(0, self.ld - base.ld),
            dd=max(0, self.dd - base.dd),
            dw=max(0, self.dw - base.dw),
            ww=max(0, self.ww - base.ww),
            crashes=max(0, self.crashes - base.crashes),
            timelosses=max(0, self.timelosses - base.timelosses),
            illegals=max(0, self.illegals - base.illegals),
        )

    def is_empty(self) -> bool:
        return (
            self.games == 0
            and self.ll == 0
            and self.ld == 0
            and self.dd == 0
            and self.dw == 0
            and self.ww == 0
            and self.crashes == 0
            and self.timelosses == 0
            and self.illegals == 0
        )


@dataclass(slots=True)
class OpenBenchClientConfig:
    enabled: bool
    mode: Literal["existing_test", "create_test"]
    server: str
    username: str
    password: str
    target_test_id: int | None
    submit_interval_games: int
    strict: bool
    heartbeat_interval_sec: float
    poll_interval_sec: float
    assignment_timeout_sec: float
    allow_insecure_http: bool
    concurrency: int
    create_payload: dict[str, str] | None = None
    create_discovery_timeout_sec: float = 180.0

    def validate(self) -> None:
        parsed = urlparse(self.server)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("openbench.server must start with http:// or https://")
        if parsed.scheme != "https" and not self.allow_insecure_http:
            raise ValueError("openbench.server must use https:// unless allow_insecure_http=true")
        if not parsed.netloc:
            raise ValueError("openbench.server must include host")
        if self.submit_interval_games <= 0:
            raise ValueError("openbench.submit_interval_games must be > 0")
        if self.heartbeat_interval_sec <= 0:
            raise ValueError("openbench.heartbeat_interval_sec must be > 0")
        if self.mode not in {"existing_test", "create_test"}:
            raise ValueError("openbench.mode must be 'existing_test' or 'create_test'")
        if self.mode == "existing_test":
            if self.target_test_id is None or self.target_test_id <= 0:
                raise ValueError("openbench.target_test_id must be > 0 in existing_test mode")
        if self.mode == "create_test" and not self.create_payload:
            raise ValueError("openbench.create payload is required in create_test mode")


class OpenBenchClient:
    def __init__(self, config: OpenBenchClientConfig, *, tested_engine: str, base_engine: str) -> None:
        config.validate()
        self._config = config
        self._tested_engine = tested_engine
        self._base_engine = base_engine
        self._http: aiohttp.ClientSession | None = None
        self._machine_id: int | None = None
        self._secret: str | None = None
        self._workload_test_id: int | None = None
        self._result_id: int | None = None
        self._submitted = OpenBenchCounters()
        self._last_synced_games = 0
        self._blacklist: set[int] = set()
        self._has_assignment = False
        self._client_version = 39
        self._worker_compilers: dict[str, tuple[str, str]] = {}
        self._worker_tokens: dict[str, bool] = {}

    @property
    def strict(self) -> bool:
        return self._config.strict

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def initialize(self) -> None:
        timeout = aiohttp.ClientTimeout(total=30)
        self._http = aiohttp.ClientSession(timeout=timeout)
        await self._verify_credentials()
        await self._prepare_worker_capabilities()
        await self._ensure_target_test_id()
        await self._register_worker()
        await self._claim_target_workload()

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    def snapshot_state(self) -> dict[str, object]:
        return {
            "submitted": self._submitted.to_state(),
            "last_synced_games": self._last_synced_games,
            "target_test_id": self._config.target_test_id,
            "claimed_test_id": self._workload_test_id,
            "result_id": self._result_id,
            "blacklist": sorted(self._blacklist),
        }

    def restore_state(self, state: dict[str, object]) -> None:
        submitted = state.get("submitted")
        if isinstance(submitted, dict):
            self._submitted = OpenBenchCounters.from_state(cast(dict[str, object], submitted))
            self._last_synced_games = self._submitted.games
        if (synced := coerce_int(state.get("last_synced_games"))) is not None:
            self._last_synced_games = synced
        if (target := coerce_int(state.get("target_test_id"))) is not None and target > 0:
            self._config.target_test_id = target
        blacklist = state.get("blacklist")
        if isinstance(blacklist, list):
            self._blacklist = {c for x in blacklist if (c := coerce_int(x)) is not None}

    async def sync(self, db: ArenaDBService) -> bool:
        if not self._has_assignment:
            return False
        totals = _compute_totals(db, tested_engine=self._tested_engine, base_engine=self._base_engine)
        if totals.games - self._last_synced_games < self._config.submit_interval_games:
            return False
        delta = totals.delta_from(self._submitted)
        if delta.is_empty():
            self._last_synced_games = totals.games
            return False
        should_stop = await self._submit_results(delta)
        self._submitted = totals
        self._last_synced_games = totals.games
        return should_stop

    async def flush(self, db: ArenaDBService) -> bool:
        if not self._has_assignment:
            return False
        totals = _compute_totals(db, tested_engine=self._tested_engine, base_engine=self._base_engine)
        delta = totals.delta_from(self._submitted)
        if delta.is_empty():
            self._last_synced_games = totals.games
            return False
        should_stop = await self._submit_results(delta)
        self._submitted = totals
        self._last_synced_games = totals.games
        return should_stop

    async def heartbeat(self) -> bool:
        if not self._has_assignment:
            return False
        return await self._heartbeat_once(allow_recovery=True)

    async def _verify_credentials(self) -> None:
        response = await self._post("clientVersionRef", self._auth_payload())
        if "error" in response:
            raise OpenBenchError(f"OpenBench authentication failed: {response['error']}")
        client_version = response.get("client_version")
        if isinstance(client_version, int):
            self._client_version = client_version

    async def _ensure_target_test_id(self) -> None:
        if self._config.mode != "create_test":
            return
        existing = self._config.target_test_id
        if isinstance(existing, int) and existing > 0:
            return
        payload = dict(self._config.create_payload or {})
        if not payload:
            raise OpenBenchError("openbench.create payload is empty")
        pre_ids = await self._fetch_user_test_ids()
        await self._submit_create_test(payload)
        created_id = await self._discover_created_test_id(
            before_ids=pre_ids,
            payload=payload,
            timeout_sec=float(self._config.create_discovery_timeout_sec),
        )
        self._config.target_test_id = created_id

    async def _register_worker(self) -> None:
        payload: dict[str, str] = {
            **self._auth_payload(),
            "system_info": json.dumps(self._build_system_info(), ensure_ascii=False),
        }
        response = await self._post("clientWorkerInfo", payload)
        if "error" in response:
            raise OpenBenchError(f"OpenBench worker registration failed: {response['error']}")
        machine_id = response.get("machine_id")
        secret = response.get("secret")
        if not isinstance(machine_id, int) or not isinstance(secret, str) or not secret:
            raise OpenBenchError("OpenBench worker registration returned invalid machine credentials")
        self._machine_id = machine_id
        self._secret = secret

    async def _prepare_worker_capabilities(self) -> None:
        try:
            response = await self._get("clientGetBuildInfo")
        except OpenBenchError as exc:
            logger.warning("Failed to fetch OpenBench build info; proceeding with minimal worker capabilities: %s", exc)
            return
        if not isinstance(response, dict):
            logger.warning("OpenBench clientGetBuildInfo returned non-object payload; ignoring")
            return
        compilers, tokens = _derive_worker_capabilities(response, target_engines=self._target_engine_names())
        self._worker_compilers = compilers
        self._worker_tokens = tokens

    def _target_engine_names(self) -> set[str]:
        names = {self._tested_engine, self._base_engine}
        payload = self._config.create_payload or {}
        for key in ("dev_engine", "base_engine"):
            raw = payload.get(key)
            if isinstance(raw, str) and raw.strip():
                names.add(raw.strip())
        return {name for name in names if name}

    async def _claim_target_workload(self) -> None:
        deadline = asyncio.get_running_loop().time() + self._config.assignment_timeout_sec
        while True:
            response = await self._post(
                "clientGetWorkload",
                {
                    "machine_id": self._machine_id,
                    "secret": self._secret,
                    "blacklist": [str(x) for x in sorted(self._blacklist)],
                },
            )
            if "error" in response:
                raise OpenBenchError(f"OpenBench workload request failed: {response['error']}")
            workload = response.get("workload")
            if isinstance(workload, dict):
                wl = cast(dict[str, object], workload)
                test_raw = wl.get("test")
                result_raw = wl.get("result")
                test = cast(dict[str, object], test_raw) if isinstance(test_raw, dict) else {}
                result = cast(dict[str, object], result_raw) if isinstance(result_raw, dict) else {}
                test_id = test.get("id")
                result_id = result.get("id")
                if test_id == self._config.target_test_id and isinstance(result_id, int):
                    self._workload_test_id = self._config.target_test_id
                    self._result_id = result_id
                    self._has_assignment = True
                    return
                if isinstance(test_id, int):
                    self._blacklist.add(test_id)
            if asyncio.get_running_loop().time() >= deadline:
                raise OpenBenchError(
                    f"OpenBench target workload assignment timed out (target_test_id={self._config.target_test_id})"
                )
            await asyncio.sleep(self._config.poll_interval_sec)

    async def _submit_create_test(self, payload: dict[str, str]) -> None:
        create_payload = {**payload, **self._auth_payload(), "action": "CREATE_TEST"}
        _status, _final_url, body = await self._post_text("scripts", create_payload, allow_redirects=True)
        if "Unable to authenticate user" in body or "Bad Credentials" in body:
            raise OpenBenchError("OpenBench CREATE_TEST authentication failed")
        error_message = _extract_error_message(body)
        if error_message:
            raise OpenBenchError(f"OpenBench CREATE_TEST failed: {error_message}")

    async def _discover_created_test_id(
        self,
        *,
        before_ids: set[int],
        payload: dict[str, str],
        timeout_sec: float,
    ) -> int:
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while True:
            ids = await self._fetch_user_test_ids()
            candidates = sorted((i for i in ids if i not in before_ids), reverse=True)
            for test_id in candidates:
                if await self._is_matching_created_test(test_id, payload):
                    return test_id
            if asyncio.get_running_loop().time() >= deadline:
                raise OpenBenchError("Failed to detect created OpenBench test id within timeout")
            await asyncio.sleep(self._config.poll_interval_sec)

    async def _fetch_user_test_ids(self) -> set[int]:
        _status, _url, body = await self._post_text(f"user/{self._config.username}", self._auth_payload())
        ids = {int(m) for m in re.findall(r"/test/(\d+)/", body)}
        return ids

    async def _is_matching_created_test(self, test_id: int, payload: dict[str, str]) -> bool:
        _status, _url, body = await self._post_text(f"test/{test_id}", self._auth_payload())
        must_include = [
            payload.get("dev_repo", ""),
            payload.get("base_repo", ""),
            payload.get("dev_engine", ""),
            payload.get("base_engine", ""),
            payload.get("dev_time_control", ""),
            payload.get("base_time_control", ""),
        ]
        for key in ("dev_branch", "base_branch"):
            value = payload.get(key, "").strip()
            if value and not re.fullmatch(r"[0-9a-fA-F]{40}", value):
                must_include.append(value)
        for token in must_include:
            if token and token not in body:
                return False
        return True

    async def _submit_results(self, delta: OpenBenchCounters) -> bool:
        return await self._submit_results_once(delta, allow_recovery=True)

    async def _submit_results_once(self, delta: OpenBenchCounters, *, allow_recovery: bool) -> bool:
        if self._workload_test_id is None or self._result_id is None:
            raise OpenBenchError("OpenBench workload is not assigned")
        payload: dict[str, str | int] = {
            "machine_id": self._machine_id or 0,
            "secret": self._secret or "",
            "test_id": self._workload_test_id,
            "result_id": self._result_id,
            **delta.to_payload(),
        }
        response = await self._post("clientSubmitResults", payload)
        if "error" in response:
            error = str(response["error"])
            if allow_recovery and self._is_recoverable_worker_error(error):
                await self._recover_worker_assignment(error=error, endpoint="clientSubmitResults")
                return await self._submit_results_once(delta, allow_recovery=False)
            raise OpenBenchError(f"OpenBench result submission failed: {error}")
        stop = response.get("stop")
        return bool(stop)

    async def _heartbeat_once(self, *, allow_recovery: bool) -> bool:
        if self._workload_test_id is None:
            raise OpenBenchError("OpenBench workload is not assigned")
        payload: dict[str, str | int] = {
            "machine_id": self._machine_id or 0,
            "secret": self._secret or "",
            "test_id": self._workload_test_id,
        }
        response = await self._post("clientHeartbeat", payload)
        if "error" in response:
            error = str(response["error"])
            if allow_recovery and self._is_recoverable_worker_error(error):
                await self._recover_worker_assignment(error=error, endpoint="clientHeartbeat")
                return await self._heartbeat_once(allow_recovery=False)
            raise OpenBenchError(f"OpenBench heartbeat failed: {error}")
        stop = response.get("stop")
        return bool(stop)

    async def _recover_worker_assignment(self, *, error: str, endpoint: str) -> None:
        logger.warning(
            "OpenBench %s returned recoverable worker error (%s); re-registering worker and reclaiming workload",
            endpoint,
            error,
        )
        self._machine_id = None
        self._secret = None
        self._workload_test_id = None
        self._result_id = None
        self._has_assignment = False
        await self._register_worker()
        await self._claim_target_workload()

    @staticmethod
    def _is_recoverable_worker_error(error: str) -> bool:
        return error.strip() in _RECOVERABLE_WORKER_ERRORS

    async def _post(self, endpoint: str, payload: Mapping[str, object]) -> dict[str, object]:
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
        return data

    async def _get(self, endpoint: str) -> dict[str, object]:
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
        return data

    async def _post_text(
        self,
        endpoint: str,
        payload: Mapping[str, object],
        *,
        allow_redirects: bool = True,
    ) -> tuple[int, str, str]:
        if self._http is None:
            raise OpenBenchError("OpenBench HTTP session is not initialized")
        url = self._url(endpoint)
        try:
            async with self._http.post(url, data=payload, allow_redirects=allow_redirects) as response:
                response.raise_for_status()
                data = await response.text()
                final_url = str(response.url)
                return int(response.status), final_url, data
        except aiohttp.ClientResponseError as exc:
            raise OpenBenchError(f"OpenBench request failed [{exc.status}] {endpoint}") from exc
        except aiohttp.ClientError as exc:
            raise OpenBenchError(f"OpenBench network error at {endpoint}: {exc}") from exc

    def _auth_payload(self) -> _AuthPayload:
        return {
            "username": self._config.username,
            "password": self._config.password,
        }

    def _url(self, endpoint: str) -> str:
        return f"{self._config.server.rstrip('/')}/{endpoint.lstrip('/')}"

    def _build_system_info(self) -> dict[str, object]:
        cpu = get_cpu_info()
        raw_flags = cpu.get("flags", [])
        flags = [str(x).replace("_", "").replace(".", "").upper() for x in raw_flags if isinstance(x, str)]
        compilers = {name: [spec[0], spec[1]] for name, spec in sorted(self._worker_compilers.items())}
        tokens = {name: bool(value) for name, value in sorted(self._worker_tokens.items())}
        return {
            "compilers": compilers,
            "tokens": tokens,
            "cpu_flags": sorted(set(flags)),
            "cpu_name": str(cpu.get("brand_raw") or platform.processor() or "Unknown"),
            "os_name": platform.system(),
            "os_ver": platform.release(),
            "python_ver": platform.python_version(),
            "mac_address": hex(uuid.getnode()).upper()[2:],
            "logical_cores": int(psutil.cpu_count(logical=True) or 1),
            "physical_cores": int(psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 1),
            "ram_total_mb": int(psutil.virtual_memory().total // (1024**2)),
            "machine_id": "None",
            "machine_name": "shogiarena",
            "concurrency": int(max(1, self._config.concurrency)),
            "sockets": 1,
            "syzygy_max": 0,
            "noisy": False,
            "focus": [],
            "cxx_comp": "unknown",
            "fastchess_ver": "0",
            "shogitest_ver": "0",
            "client_ver": int(self._client_version),
        }


def _derive_worker_capabilities(
    build_info: dict[str, object],
    *,
    target_engines: set[str] | None = None,
) -> tuple[dict[str, tuple[str, str]], dict[str, bool]]:
    target_set = {name for name in (target_engines or set()) if isinstance(name, str) and name}
    compilers: dict[str, tuple[str, str]] = {}
    tokens: dict[str, bool] = {}

    for engine_name_raw, engine_info_raw in build_info.items():
        if not isinstance(engine_name_raw, str):
            continue
        engine_name = engine_name_raw.strip()
        if not engine_name:
            continue
        if not isinstance(engine_info_raw, dict):
            continue
        engine_info = cast(dict[str, object], engine_info_raw)
        is_private = bool(engine_info.get("private"))
        if is_private:
            # ShogiArena submits externally generated results and does not fetch private artifacts here.
            # Advertise token availability so target workloads are not rejected at assignment time.
            tokens[engine_name] = True
            continue

        candidate = _select_available_compiler(engine_info.get("compilers"))
        compilers[engine_name] = (candidate or "shogiarena", "0")

    for engine_name in target_set:
        info_raw = build_info.get(engine_name)
        if not isinstance(info_raw, dict):
            continue
        info = cast(dict[str, object], info_raw)
        if bool(info.get("private")):
            tokens[engine_name] = True
        else:
            compilers.setdefault(engine_name, ("shogiarena", "0"))

    return compilers, tokens


def _select_available_compiler(raw_candidates: object) -> str | None:
    if not isinstance(raw_candidates, list):
        return None
    for raw in raw_candidates:
        if not isinstance(raw, str):
            continue
        candidate = raw.strip()
        if not candidate:
            continue
        executable = candidate.split(">=", 1)[0].strip()
        if executable and shutil.which(executable):
            return executable
    return None


def _round_index_from_game_name(game_name: str) -> int | None:
    match = re.match(r"^g(?P<round>\d+)-", game_name)
    if match is None:
        return None
    try:
        one_based = int(match.group("round"))
    except (TypeError, ValueError):
        return None
    if one_based <= 0:
        return None
    return one_based - 1


def _extract_error_message(body: str) -> str | None:
    # OpenBench stores errors in <div class="error-message"><pre>...</pre></div>.
    match = re.search(
        r'<div class="error-message">\s*<pre>(?P<msg>.*?)</pre>\s*</div>',
        body,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    message = re.sub(r"\s+", " ", match.group("msg").strip())
    return message or None


def _compute_totals(db: ArenaDBService, *, tested_engine: str, base_engine: str) -> OpenBenchCounters:
    games = db.get_games_with_players()
    relevant: list[GameRecordPlayersDict] = []
    totals = OpenBenchCounters()
    for game in games:
        black = str(game.get("black_player", ""))
        white = str(game.get("white_player", ""))
        if {black, white} != {tested_engine, base_engine}:
            continue
        result_raw = game["result"]
        relevant.append(game)
        tested_is_black = black == tested_engine
        tested_score = _score_from_tested(result_raw, tested_is_black=tested_is_black)
        if tested_score >= 1.0:
            totals.wins += 1
        elif tested_score <= 0.0:
            totals.losses += 1
        else:
            totals.draws += 1

        if result_raw in {GameResult.BLACK_WIN_BY_TIMEOUT, GameResult.WHITE_WIN_BY_TIMEOUT}:
            totals.timelosses += 1
        if result_raw in {GameResult.BLACK_WIN_BY_ILLEGAL_MOVE, GameResult.WHITE_WIN_BY_ILLEGAL_MOVE}:
            totals.illegals += 1
        if result_raw in {_GAME_RESULT_ERROR, GameResult.INVALID}:
            totals.crashes += 1

    # Pentanomial:
    # - Prefer deterministic pairing via round token in game_name ("g0001-..."), two rounds per opening pair.
    # - Fallback to legacy orientation-based pairing when game_name is unavailable.
    round_groups: dict[tuple[str, int], list[GameRecordPlayersDict]] = {}
    fallback_groups: dict[str, list[GameRecordPlayersDict]] = {}
    for game in relevant:
        sfen = str(game.get("initial_sfen", "startpos"))
        round_idx = _round_index_from_game_name(str(game.get("game_name", "")))
        if round_idx is None:
            fallback_groups.setdefault(sfen, []).append(game)
            continue
        pair_slot = round_idx // 2
        round_groups.setdefault((sfen, pair_slot), []).append(game)

    def add_pairs(group: list[GameRecordPlayersDict]) -> None:
        tested_black_games = [g for g in group if str(g.get("black_player", "")) == tested_engine]
        tested_white_games = [g for g in group if str(g.get("white_player", "")) == tested_engine]
        count = min(len(tested_black_games), len(tested_white_games))
        for idx in range(count):
            g1 = tested_black_games[idx]
            g2 = tested_white_games[idx]
            r1 = g1["result"]
            r2 = g2["result"]
            score = _score_from_tested(r1, tested_is_black=True) + _score_from_tested(r2, tested_is_black=False)
            if score >= 1.99:
                totals.ww += 1
            elif score >= 1.49:
                totals.dw += 1
            elif score >= 0.99:
                totals.dd += 1
            elif score >= 0.49:
                totals.ld += 1
            else:
                totals.ll += 1

    for group in round_groups.values():
        add_pairs(group)
    for group in fallback_groups.values():
        add_pairs(group)
    return totals


def _score_from_tested(result: GameResult, *, tested_is_black: bool) -> float:
    if result.is_draw():
        return 0.5
    if tested_is_black:
        if result.is_black_win():
            return 1.0
        if result.is_white_win():
            return 0.0
    else:
        if result.is_white_win():
            return 1.0
        if result.is_black_win():
            return 0.0
    return 0.5
