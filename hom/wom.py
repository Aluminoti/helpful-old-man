import asyncio
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union
from typing import cast

import aiohttp

from hom.config import Config
from hom.config import Constants


class WomError(Exception):
    """An API failure safe to describe without exposing request credentials."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class WomClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._base = Config.HOM_BASE_API_URL

    async def moderation_request(self, method: str, path: str, **payload: Any) -> Dict[str, Any]:
        """Use the shared session/authentication without changing ticket API contracts.

        Never retry mutations: a timeout may occur after the server committed them.
        """
        body = {**payload, "adminPassword": Config.SHARED_ADMIN_PASSWORD}
        try:
            async with self._session.request(
                method,
                f"{self._base.rstrip('/')}{path}",
                json=body if method != "GET" else None,
                headers=Constants.HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                raw = await response.text()
                try:
                    data: Any = json.loads(raw) if raw else {}
                except ValueError:
                    data = {}
                if not 200 <= response.status < 300:
                    message = (
                        cast(Dict[str, Any], data).get("message")
                        if isinstance(data, dict)
                        else None
                    )
                    if not isinstance(message, str) or response.status >= 500:
                        message = f"WOM API request failed (HTTP {response.status})."
                    for secret in (Config.SHARED_ADMIN_PASSWORD, Config.HOM_API_KEY):
                        if secret:
                            message = message.replace(secret, "[redacted]")
                    raise WomError(response.status, message[:1500])
                return cast(Dict[str, Any], data) if isinstance(data, dict) else {"message": data}
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            raise WomError(
                504,
                "WOM request timed out or lost its connection. Check the status before retrying.",
            ) from exc

    async def get_group(self, group_id: Union[str, int]) -> Optional[Dict[str, Any]]:
        async with self._session.get(
            f"{self._base}/groups/{group_id}", headers=Constants.HEADERS
        ) as r:
            return await r.json() if r.status == 200 else None

    async def get_player_competitions(self, username: str) -> Optional[List[Dict[str, Any]]]:
        try:
            async with self._session.get(
                f"{self._base}/players/{username}/competitions",
                headers=Constants.HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                return await r.json() if r.status == 200 else None
        except (aiohttp.ClientError, TimeoutError):
            return None

    async def remove_competition_participant(
        self, competition_id: Union[str, int], rsn: str
    ) -> Tuple[int, str]:
        async with self._session.delete(
            f"{self._base}/competitions/{competition_id}/participants",
            json={"participants": [rsn], "adminPassword": Config.SHARED_ADMIN_PASSWORD},
            headers=Constants.HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            return r.status, await r.text()

    async def verify_group(self, group_id: str) -> bool:
        async with self._session.put(
            f"{self._base}/groups/{group_id}/verify",
            json={"adminPassword": Config.SHARED_ADMIN_PASSWORD},
            headers=Constants.HEADERS,
        ) as r:
            return r.status == 200

    async def reset_group_code(self, group_id: Union[str, int]) -> Optional[Dict[str, Any]]:
        try:
            async with self._session.put(
                f"{self._base}/groups/{group_id}/reset-code",
                json={"adminPassword": Config.SHARED_ADMIN_PASSWORD},
                headers=Constants.HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                return await r.json() if r.status == 200 else None
        except (aiohttp.ClientError, TimeoutError):
            return None

    async def remove_player_group(
        self, rsn: str, group_id: Union[str, int]
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self._session.delete(
                f"{self._base}/groups/{group_id}/members",
                json={"members": [rsn], "adminPassword": Config.SHARED_ADMIN_PASSWORD},
                headers=Constants.HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                return await r.json() if r.status == 200 else None
        except (aiohttp.ClientError, TimeoutError):
            return None
