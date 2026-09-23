import asyncio
from urllib.parse import quote, urlencode
from datetime import UTC
import httpx


class IntegrationError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class WiseOldMan:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(base_url="https://api.wiseoldman.net/v2/", timeout=15,
                                                headers={"User-Agent": "discord-tracker/0.1"})

    async def request(self, method, path, payload=None, *, expect_list=False):
        for attempt in range(3 if method == "GET" else 1):
            try:
                response = await self.client.request(method, path, json=payload)
            except httpx.RequestError:
                if method == "GET" and attempt < 2:
                    await asyncio.sleep(attempt + 1)
                    continue
                raise IntegrationError("Wise Old Man connection failed; try again later") from None
            if response.status_code == 429:
                raise IntegrationError("Wise Old Man rate limit reached; wait before retrying", 429)
            if response.status_code >= 500 and method == "GET" and attempt < 2:
                await asyncio.sleep(attempt + 1)
                continue
            if response.status_code == 404:
                raise IntegrationError("Requested resource was not found on Wise Old Man", 404)
            if response.is_error:
                raise IntegrationError(f"Wise Old Man rejected the operation (HTTP {response.status_code})", response.status_code)
            if method == 'DELETE' and response.status_code == 204:
                return {}
            try:
                result = response.json()
                if not isinstance(result, list if expect_list else dict):
                    raise ValueError()
                return result
            except ValueError:
                raise IntegrationError("Wise Old Man returned an unexpected response") from None

    async def player(self, username=None, player_id=None, refresh=False):
        path = f"players/id/{player_id}" if player_id is not None else f"players/{quote(username, safe='')}"
        result = await self.request("POST" if refresh else "GET", path)
        if not isinstance(result.get("id"), int) or not isinstance(result.get("username"), str):
            raise IntegrationError("Wise Old Man returned an invalid player record")
        return result

    async def close(self):
        await self.client.aclose()

    async def group_gains(self, group_id, start, end):
        query = urlencode({'startDate': start.astimezone(UTC).isoformat(),
                           'endDate': end.astimezone(UTC).isoformat()})
        rows = await self.request('GET', f'groups/{group_id}/bulk-gained?{query}', expect_list=True)
        result = {}
        for row in rows:
            player = row.get('player') if isinstance(row, dict) else None
            player_id = player.get('id') if isinstance(player, dict) else None
            if type(player_id) is not int or player_id in result:
                raise IntegrationError('Wise Old Man returned invalid or duplicate group players')
            result[player_id] = row
        return result

    async def group_competitions(self, group_id):
        records = {}
        # Exhaust the bounded search before declaring a match unique.
        for page in range(10):
            batch = await self.request('GET', f'groups/{group_id}/competitions?limit=20&offset={page * 20}', expect_list=True)
            for item in batch:
                if not isinstance(item, dict) or not isinstance(item.get('id'), int):
                    raise IntegrationError('Invalid competition list; reconciliation requires manager review')
                records[item['id']] = item
            if len(batch) < 20:
                return list(records.values())
            await asyncio.sleep(0.2)
        raise IntegrationError('Competition search exceeded 200 records; use /event-reconcile for manual review')
