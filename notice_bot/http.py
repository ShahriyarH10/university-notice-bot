from __future__ import annotations

import logging
import time

import cloudscraper
import requests

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """A page could not be downloaded, even after retrying."""


def create_session() -> requests.Session:
    # aiub.edu sits behind Cloudflare, which rejects plain `requests` clients.
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )


def fetch_html(
    session: requests.Session,
    url: str,
    *,
    timeout: float = 30.0,
    attempts: int = 3,
    backoff: float = 2.0,
) -> str:
    """GET `url` and return the body, retrying transient failures."""
    return _get(session, url, timeout=timeout, attempts=attempts, backoff=backoff).text


def fetch_file(
    session: requests.Session,
    url: str,
    *,
    max_bytes: int,
    timeout: float = 30.0,
    attempts: int = 3,
    backoff: float = 2.0,
) -> bytes:
    """GET `url` and return the raw bytes, refusing anything over `max_bytes`."""
    content = _get(session, url, timeout=timeout, attempts=attempts, backoff=backoff).content
    if len(content) > max_bytes:
        raise FetchError(f"{url} is {len(content)} bytes, over the {max_bytes}-byte limit")
    return content


def _get(
    session: requests.Session, url: str, *, timeout: float, attempts: int, backoff: float
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            log.info("Fetching %s (attempt %d/%d)", url, attempt, attempts)
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            log.warning("Fetch of %s failed: %s", url, exc)
            if attempt < attempts:
                time.sleep(backoff * attempt)
    raise FetchError(f"could not fetch {url} after {attempts} attempts: {last_error}")
