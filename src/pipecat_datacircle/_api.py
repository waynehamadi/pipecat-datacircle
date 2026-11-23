"""One HTTPS GET to Datacircle's open API (https://api.datacircle.dev/v1), no key, and what its answers are cut to for a model."""

import json
import math
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

BASE_URL = "https://api.datacircle.dev/v1"
TIMEOUT = 30.0
USER_AGENT = "pipecat-datacircle/0.1.0"

# A plan holds about 90 keys (its terms of use, its aggregators...): the tools keep the ones that choose a plan, the rest is on its page.
PLAN_KEYS = ["api", "rank", "provider", "plan", "score", "perThousand", "notFoundPerThousand", "billing", "pay",
             "currency", "units", "unit", "strengths", "latencyMs", "page", "pricingPage", "docs"]
API_KEYS = ["api", "slug", "plans", "providers", "best", "bestPage", "sheetPage"]


class DatacircleError(Exception):
    """The API's refusal in its own words, why it could not be reached, or an argument it can't take: a function's
    handler gives the model its words as the call's result, {"error": ...}. status is the API's HTTP status, None when
    it gave none; retryable, whether the same call may be answered later (the API out of reach, busy or failing)."""

    def __init__(self, message: str, status: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def brief(item: Dict[str, Any], keys: List[str] = PLAN_KEYS) -> Dict[str, Any]:
    return {key: item[key] for key in keys if item.get(key) is not None}


def slug(value: Any) -> str:
    """The API's rule for a slug: the name in lowercase letters and digits, the rest a hyphen."""
    plain = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def whole(name: str, value: Any, low: int, high: Optional[int] = None) -> Optional[int]:
    """A whole number from low to high, or None when left out: a plain call may send one as text."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float("nan")
    if isinstance(value, bool) or not math.isfinite(number) or number != int(number):
        raise DatacircleError(f"{name} must be a whole number, not {value!r}")
    if number < low or (high is not None and number > high):
        raise DatacircleError(f"{name} must be from {low} to {high}, not {int(number)}" if high is not None
                              else f"{name} must be {low} or more, not {int(number)}")
    return int(number)


def _words(body: bytes) -> str:
    """A refusal's words: the API's error, with every slug when the slug was unknown."""
    text = body.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
        if parsed.get("slugs"):
            return f"{parsed['error']}; the slugs: {', '.join(parsed['slugs'])}"
        return str(parsed.get("error", text))
    except (ValueError, KeyError, TypeError, AttributeError):
        return text


def get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """The API's JSON answer, or a DatacircleError with its words: waits once when it answers 429."""
    query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value not in (None, "")})
    url = f"{BASE_URL.rstrip('/')}{path}" + (f"?{query}" if query else "")
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
                body = answer.read()
        except urllib.error.HTTPError as error:
            body = error.read()
            if error.code == 429 and not attempt:
                try:
                    wait = int(error.headers.get("Retry-After") or 30)
                except ValueError:
                    wait = 30
                time.sleep(min(max(wait, 1), 60))
                continue
            if error.code == 429:
                raise DatacircleError("Datacircle's API answered 429 twice: wait a minute and call again", 429, True) from None
            raise DatacircleError(f"Datacircle's API answered {error.code}: {_words(body)[:3000]}", error.code,
                                  error.code >= 500) from None
        except (urllib.error.URLError, OSError) as error:
            raise DatacircleError(f"Datacircle's API could not be reached: {getattr(error, 'reason', error)}",
                                  retryable=True) from None
        try:
            return json.loads(body)
        except ValueError as error:
            raise DatacircleError(f"Datacircle's API gave no JSON: {error}") from None
