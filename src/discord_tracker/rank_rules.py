"""Validated, file-owned policy; no executable expressions in configuration."""
import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError('Duplicate policy key')
            result[key] = self.construct_object(value_node, deep=deep)
        return result


@dataclass(frozen=True)
class RankRules:
    enabled: bool
    daily_at: time
    minimum_gain: int
    ranks: tuple[tuple[str, int], ...]
    protected: tuple[str, ...]
    document: str
    digest: str

    @classmethod
    def load(cls, path=Path('config/ranks.yaml')):
        try:
            raw = yaml.load(path.read_text(encoding='utf-8'), Loader=UniqueKeyLoader)
            if not isinstance(raw, dict) or set(raw) != {'version', 'enabled', 'daily_at', 'activity', 'protected_ranks', 'ranks'}:
                raise ValueError()
            if type(raw['version']) is not int or raw['version'] != 1 or type(raw['enabled']) is not bool:
                raise ValueError()
            clock = time.fromisoformat(raw['daily_at'])
            if clock.tzinfo or clock.second or clock.microsecond or len(raw['daily_at']) != 5:
                raise ValueError()
            activity = raw['activity']
            if set(activity) != {'period', 'metric', 'minimum_gain'} or activity['period'] != 'previous_calendar_month' or activity['metric'] != 'overall_xp':
                raise ValueError()
            if type(activity['minimum_gain']) is not int or activity['minimum_gain'] < 1:
                raise ValueError()
            protected = raw['protected_ranks']
            if not isinstance(protected, list) or not all(isinstance(n, str) and n.strip() == n and 0 < len(n) <= 80 for n in protected):
                raise ValueError()
            # These staff ranks must remain protected even when editing the policy.
            if not {'moderator', 'captain', 'lieutenant', 'commander'} <= {n.casefold() for n in protected}:
                raise ValueError()
            ranks = raw['ranks']
            if not isinstance(ranks, list) or not ranks:
                raise ValueError()
            names = {n.casefold() for n in protected}
            previous = 0
            for rank in ranks:
                if not isinstance(rank, dict) or set(rank) != {'name', 'months'}:
                    raise ValueError()
                name, months = rank['name'], rank['months']
                if not isinstance(name, str) or not 0 < len(name) <= 80 or name.strip() != name or name.casefold() in names:
                    raise ValueError()
                if type(months) is not int or not previous < months <= 1200:
                    raise ValueError()
                names.add(name.casefold())
                previous = months
            document = json.dumps(raw, sort_keys=True)
            return cls(raw['enabled'], clock, activity['minimum_gain'],
                       tuple((r['name'], r['months']) for r in ranks), tuple(protected),
                       document, hashlib.sha256(document.encode()).hexdigest())
        except (OSError, yaml.YAMLError, ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Invalid config/ranks.yaml; check its schema, schedule and increasing rank thresholds') from None

    def calculated(self, joined: date, today: date):
        result = None
        for name, months in self.ranks:
            year, month = divmod(joined.year * 12 + joined.month - 1 + months, 12)
            if year > 9999:
                continue
            anniversary = date(year, month + 1, min(joined.day, calendar.monthrange(year, month + 1)[1]))
            if anniversary <= today:
                result = name
        return result


def previous_month(instant: datetime, timezone: str):
    local = instant.astimezone(ZoneInfo(timezone))
    end = datetime(local.year, local.month, 1, tzinfo=local.tzinfo)
    start = (end - timedelta(days=1)).replace(day=1)
    return start, end
