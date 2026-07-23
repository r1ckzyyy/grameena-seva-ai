"""Official-source search and page extraction."""

from __future__ import annotations

import json
from urllib.parse import urlparse


def _is_official(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "myscheme.gov.in" or host.endswith(".myscheme.gov.in") or host == "gov.in" or host.endswith(".gov.in")


def search_schemes(query: str, state: str, api_key: str) -> str:
    from tavily import TavilyClient

    response = TavilyClient(api_key=api_key).search(
        query=f"{query} {state} agricultural subsidy site:myscheme.gov.in OR site:gov.in",
        search_depth="advanced",
        max_results=5,
        include_domains=["myscheme.gov.in", "gov.in"],
    )
    hits = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:1200]}
        for r in response.get("results", [])
        if _is_official(r.get("url", ""))
    ]
    return json.dumps({"results": hits}, ensure_ascii=False)


def get_scheme_details(url: str, api_key: str) -> str:
    if not _is_official(url):
        return ""

    # firecrawl-py 2.5.x exposes FirecrawlApp; newer releases expose
    # Firecrawl. Support both so a package/API naming change cannot break the
    # entire conversation after search succeeds.
    import firecrawl

    client_class = getattr(firecrawl, "Firecrawl", None) or getattr(firecrawl, "FirecrawlApp", None)
    if client_class is None:
        raise RuntimeError("Installed firecrawl-py does not expose a supported client")
    client = client_class(api_key=api_key)
    if hasattr(client, "scrape"):
        doc = client.scrape(url, formats=["markdown"])
    else:
        # firecrawl-py 2.x uses keyword arguments directly. Passing the old
        # v1 ``params`` envelope causes a 400 "Unrecognized key" response.
        doc = client.scrape_url(url, formats=["markdown"])
    markdown = doc.markdown if hasattr(doc, "markdown") else doc.get("markdown", "")
    return str(markdown)[:8000]
