"""yfinance-based news data fetching functions."""

import contextlib
from datetime import UTC, datetime

import yfinance as yf
from dateutil.relativedelta import relativedelta
import email.utils
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.date_window import coverage_gap, in_window
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.symbols import normalize_symbol
from tradingagents.dataflows.vendors.yahoo.common import yf_retry


def _extract_article_data(article: dict) -> dict:
    """Extract article data from yfinance news format (handles nested 'content' structure).

    Yahoo sends some fields as null rather than leaving them out, so each falls
    back to its default on a null as well as on a missing key (#1458).
    """
    content = article.get("content")
    if isinstance(content, dict):
        url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
        pub_date_str = content.get("pubDate") or ""
        pub_date = None
        if pub_date_str:
            with contextlib.suppress(ValueError, AttributeError):
                pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))

        return {
            "title": content.get("title") or "No title",
            "summary": content.get("summary") or "",
            "publisher": (content.get("provider") or {}).get("displayName") or "Unknown",
            "link": url_obj.get("url") or "",
            "pub_date": pub_date,
        }
    # Fallback for flat structure. Parse the epoch publish time so flat
    # articles are date-filterable too (otherwise they bypass the
    # historical window and leak future news, #992/#1007).
    pub_date = None
    ts = article.get("providerPublishTime")
    if ts:
        # Epoch seconds are UTC; parse them as UTC-aware so filtering does
        # not shift with the host timezone (#1126).
        with contextlib.suppress(ValueError, OSError, TypeError):
            pub_date = datetime.fromtimestamp(ts, tz=UTC)
    return {
        "title": article.get("title") or "No title",
        "summary": article.get("summary") or "",
        "publisher": article.get("publisher") or "Unknown",
        "link": article.get("link") or "",
        "pub_date": pub_date,
    }


def _search_news(canonical: str, limit: int) -> tuple[list, bool]:
    """Yahoo search's articles tagged with ``canonical``, and whether it knows the symbol.

    Search answers when the quote feed is empty, but its articles are a
    relevance-picked sample, and some are tagged loosely; only those tagged with
    the symbol are about it.
    """
    search = yf_retry(lambda: yf.Search(canonical, news_count=limit))
    if search is None:  # Yahoo answered "not found": its search is down
        return [], False
    tagged = [
        a for a in (search.news or [])
        if canonical in (t.upper() for t in a.get("relatedTickers") or [])
    ]
    knows = any((q.get("symbol") or "").upper() == canonical for q in search.quotes or [])
    return tagged, knows or bool(search.news)



def _fetch_live_rss_news(query: str, limit: int = 10) -> list[dict]:
    """Fetch live real-time breaking news from Google News RSS and CoinTelegraph."""
    articles = []
    encoded_query = urllib.parse.quote_plus(query)
    urls = [
        f"https://news.google.com/rss/search?q={encoded_query}+when:7d&hl=en-US&gl=US&ceid=US:en",
    ]
    if any(k in query.lower() for k in ["btc", "bitcoin", "crypto", "eth", "ethereum", "sol"]):
        urls.append("https://cointelegraph.com/rss")

    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                root = ET.fromstring(resp.read())
                for item in root.findall(".//item"):
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    desc = (item.findtext("description") or "").strip()
                    pub_date_str = item.findtext("pubDate") or ""
                    pub_dt = None
                    if pub_date_str:
                        with contextlib.suppress(Exception):
                            pub_dt = email.utils.parsedate_to_datetime(pub_date_str)
                            if pub_dt and pub_dt.tzinfo is None:
                                pub_dt = pub_dt.replace(tzinfo=UTC)

                    source = "Live News"
                    source_elem = item.find("source")
                    if source_elem is not None and source_elem.text:
                        source = source_elem.text
                    elif "cointelegraph" in url:
                        source = "CoinTelegraph"

                    if title:
                        articles.append({
                            "title": title,
                            "summary": desc[:280] if desc and "<" not in desc else "",
                            "publisher": source,
                            "link": link,
                            "pub_date": pub_dt,
                        })
                    if len(articles) >= limit * 2:
                        break
        except Exception:
            continue
    return articles

def _format_articles(articles: list[dict]) -> str:
    out = ""
    for data in articles:
        out += f"### {data['title']} (source: {data['publisher']})\n"
        if data["summary"]:
            out += f"{data['summary']}\n"
        if data["link"]:
            out += f"Link: {data['link']}\n"
        out += "\n"
    return out


def get_news_yfinance(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """News about ``ticker`` published in the window, from Yahoo Finance.

    The symbol's quote feed comes first. When it is empty, Yahoo search serves
    the articles tagged with the symbol; when it has none, or neither answers,
    Yahoo news is unavailable, which is not an absence of news, and a configured
    next vendor is tried. Search results are a sample, so finding none in the
    window never reads as "no news".
    """
    article_limit = get_config()["news_article_limit"]
    # Query Yahoo with the canonical symbol, like every other yfinance path —
    # a raw broker/forex/crypto alias (XAUUSD, BTCUSD) otherwise silently
    # returns no news. Keep the user's ticker in the report header.
    canonical = normalize_symbol(ticker)
    resolved = "" if canonical == ticker else f" (resolved to {canonical})"
    subject = f"news for {ticker}{resolved}"
    feed = yf_retry(lambda: yf.Ticker(canonical).get_news(count=article_limit)) or []
    articles = [_extract_article_data(a) for a in feed]
    if not feed:
        found, answered = _search_news(canonical, article_limit)
        if not answered:
            raise VendorUnavailableError(f"Yahoo Finance returned no news response for {canonical}")
        if not found:
            # With the quote feed empty, no tagged article says nothing about the
            # company's news (Yahoo tags no article with most non-US listings).
            raise VendorUnavailableError(f"Yahoo Finance has no news articles tagged {canonical}")
        articles = [_extract_article_data(a) for a in found]

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    # Keep only articles within the requested window (look-ahead safe).
    in_range = [a for a in articles if in_window(a["pub_date"], start_dt, end_dt)]

    # Enrich with live breaking RSS news if results are sparse or for crypto assets
    if len(in_range) < 5 or any(k in canonical.upper() for k in ["BTC", "ETH", "SOL", "CRYPTO"]):
        q = f"{canonical} Bitcoin Crypto" if "BTC" in canonical.upper() else f"{canonical} stock market news"
        live_arts = _fetch_live_rss_news(q, limit=article_limit)
        seen = {a["title"].lower().strip() for a in in_range}
        for a in live_arts:
            if a["title"].lower().strip() not in seen:
                if in_window(a["pub_date"], start_dt, end_dt):
                    seen.add(a["title"].lower().strip())
                    in_range.append(a)

    if in_range:
        return f"## {ticker}{resolved} News, from {start_date} to {end_date}:\n\n{_format_articles(in_range)}"

    if not feed:
        return (f"<Yahoo Finance news unavailable for {start_date}..{end_date}: Yahoo search "
                f"returns only a sample of recent articles about {canonical}, so this is not "
                f"an absence of {subject}>")
    gap = coverage_gap(
        (a["pub_date"] for a in articles),
        start_date, end_date, "Yahoo Finance news", subject,
    )
    return gap or f"No news found for {ticker}{resolved} between {start_date} and {end_date}"


def get_global_news_yfinance(
    as_of_date: str,
    look_back_days: int | None = None,
    limit: int | None = None,
) -> str:
    """
    Retrieve global/macro economic news using yfinance Search.

    Args:
        as_of_date: Current date in yyyy-mm-dd format
        look_back_days: Number of days to look back. ``None`` falls back to
            ``global_news_lookback_days`` from the active config.
        limit: Maximum number of articles to return. ``None`` falls back to
            ``global_news_article_limit`` from the active config.

    Returns:
        Formatted string containing global news articles
    """
    config = get_config()
    if look_back_days is None:
        look_back_days = config["global_news_lookback_days"]
    if limit is None:
        limit = config["global_news_article_limit"]
    search_queries = config["global_news_queries"]

    curr_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_dt = curr_dt - relativedelta(days=look_back_days)
    start_date = start_dt.strftime("%Y-%m-%d")

    in_window_news = []
    seen_titles = set()

    for query in search_queries:
        found = yf_retry(lambda q=query: yf.Search(
            query=q,
            news_count=limit,
            enable_fuzzy_query=True,
        ).news)

        for article in found or []:
            # Window first: the limit counts what the run may read, so an
            # out-of-window article must not spend the budget or cut the
            # remaining searches short (#1356). Flat articles are filtered
            # on the same rule, so none can leak future news (#1007).
            data = _extract_article_data(article)
            if not in_window(data["pub_date"], start_dt, curr_dt):
                continue
            if data["title"] and data["title"] not in seen_titles:
                seen_titles.add(data["title"])
                in_window_news.append(data)

        if len(in_window_news) >= limit:
            break

    # Enrich with live breaking macroeconomic and crypto news
    if len(in_window_news) < limit:
        extra_rss = _fetch_live_rss_news("Federal Reserve interest rates inflation economic outlook", limit=5)
        extra_rss += _fetch_live_rss_news("Bitcoin cryptocurrency global markets financial news", limit=5)
        for a in extra_rss:
            if a["title"] and a["title"] not in seen_titles:
                if in_window(a["pub_date"], start_dt, curr_dt):
                    seen_titles.add(a["title"])
                    in_window_news.append(a)
            if len(in_window_news) >= limit:
                break

    news_str = _format_articles(in_window_news[:limit])

    # Nothing fell inside the window -> say so rather than return an
    # empty-bodied report (#993).
    if not news_str:
        # Results merge several fuzzy searches, so their timestamps prove no
        # continuous coverage; judge the window against the present only.
        gap = coverage_gap((), start_date, as_of_date, "Yahoo Finance global news", "market news")
        return gap or f"No global news found between {start_date} and {as_of_date}"

    return f"## Global Market News, from {start_date} to {as_of_date}:\n\n{news_str}"
