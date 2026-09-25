"""P2: Nimble web search for unknown errors (brief 23.5).

get_evidence(failure) searches "pandas 2.0 <signature>" on pandas.pydata.org first; only if that
finds nothing does it search the whole web, preferring pandas.pydata.org and github.com/pandas-dev,
else taking the top result. It returns ~500 characters around the API name plus the URL and total
latency, and returns None, never raises, when NIMBLE_API_KEY is empty or the search fails.

`seen_urls` holds every URL Nimble returned this run: a rule's source_url must be one of them.
`last_lookup` describes the latest call: {"query", "url", "latency_s", "searches", "error"}.
"""
import os
import re
import time
from urllib.parse import urlparse

import requests

from evergreen.schema import Evidence, Failure

API = "https://sdk.nimbleway.com/v2/search"
PREFERRED = ("pandas.pydata.org", "github.com/pandas-dev")
DOC_DOMAINS = ["pandas.pydata.org"]   # searched first; include_domains takes hosts, not paths
MAX_RESULTS = 5
SEARCH_DEPTH = "standard"   # page text arrives in `description` ("lite" gives short snippets only)
SNIPPET_CHARS = 500
TIMEOUT_S = 20

seen_urls: set[str] = set()
last_lookup: dict = {}


def query_for(failure: Failure) -> str:
    return f"pandas 2.0 {failure.signature}"


def get_evidence(failure: Failure) -> Evidence | None:
    global last_lookup
    query = query_for(failure)
    last_lookup = {"query": query, "url": None, "latency_s": 0.0, "searches": 0, "error": None}
    key = os.environ.get("NIMBLE_API_KEY", "")
    if not key:
        last_lookup["error"] = "NIMBLE_API_KEY is empty"
        return None
    t0 = time.perf_counter()
    results = []
    for domains in (DOC_DOMAINS, None):
        try:
            results = _search(key, query, domains)
        except (requests.RequestException, ValueError, AttributeError) as e:
            last_lookup["error"] = f"{type(e).__name__}: {e}"
        if results:
            break
    last_lookup["latency_s"] = latency = round(time.perf_counter() - t0, 3)
    if not results:
        last_lookup["error"] = last_lookup["error"] or "no results"
        return None
    best = pick(results)
    last_lookup.update(url=best["url"], error=None)
    return Evidence(url=best["url"], snippet=snippet(best, failure.signature), latency_s=latency)


def _search(key: str, query: str, domains: list[str] | None) -> list[dict]:
    body = {"query": query, "max_results": MAX_RESULTS, "search_depth": SEARCH_DEPTH}
    if domains:
        body["include_domains"] = domains
    last_lookup["searches"] += 1
    r = requests.post(API, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=TIMEOUT_S)
    r.raise_for_status()
    results = [x for x in r.json().get("results") or [] if isinstance(x, dict) and x.get("url")]
    seen_urls.update(x["url"] for x in results)
    return results


def preferred(url: str) -> bool:
    u = urlparse(url)
    where = u.netloc.lower().removeprefix("www.") + u.path
    return any(where == p or where.startswith(p + "/") for p in PREFERRED)


def pick(results: list[dict]) -> dict:
    return next((x for x in results if preferred(x["url"])), results[0])


def snippet(result: dict, signature: str, n: int = SNIPPET_CHARS) -> str:
    """About `n` characters around the API name, from content, else from description."""
    texts = [" ".join((result.get(k) or "").split()) for k in ("content", "description")]
    for text in texts:
        for name in api_names(signature):
            i = text.find(name)
            if i < 0:
                i = text.lower().find(name.lower())
            if i >= 0:
                start = max(0, min(i - n // 2, len(text) - n))
                return text[start:start + n]
    return next((t[:n] for t in texts if t), "")


def api_names(signature: str) -> list[str]:
    """Most specific first: "AttributeError: DataFrame.append" -> ["DataFrame.append", "append", "DataFrame"]."""
    detail = signature.split(":", 1)[-1]
    dotted = [w for w in re.findall(r"[A-Za-z_][\w.]*\w", detail) if "." in w]
    words = [w for w in re.findall(r"[A-Za-z_]\w*", detail) if len(w) > 2]
    return list(dict.fromkeys(dotted + words[::-1]))
