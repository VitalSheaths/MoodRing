from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import List

import httpx
import numpy as np
from bs4 import BeautifulSoup
from dateutil import parser as dt_parser

from macro.types import FomcCommunication

BASE_URL = "https://www.federalreserve.gov"
CALENDAR_URL = f"{BASE_URL}/json/calendar.json"
FOMC_PAGE = f"{BASE_URL}/monetarypolicy/fomccalendars.htm"


def _score_hawk_dove(text: str) -> tuple[float, float]:
    t = text.lower()
    hawk_words = ["inflation", "tightening", "higher rates", "restrictive", "upside risks", "firming"]
    dove_words = ["accommodative", "support", "downside risks", "easing", "transitory", "slowdown"]
    hawk = sum(t.count(w) for w in hawk_words)
    dove = sum(t.count(w) for w in dove_words)
    return float(hawk), float(dove)


def compute_fomc_llm_score(text: str) -> float:
    """
    Computes normalized LLM/FinBERT Hawkish score in [-1.0, +1.0].
    +1.0: Ultra-hawkish (tightening / inflation focus)
    -1.0: Ultra-dovish (easing / growth support focus)
    0.0: Neutral stance
    """
    hawk, dove = _score_hawk_dove(text)
    total = hawk + dove
    if total == 0:
        return 0.0
    return float(np.clip((hawk - dove) / total, -1.0, 1.0))


def _id_from_parts(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _extract_text_from_article(html: str, comm_type: str) -> str:
    doc = BeautifulSoup(html, "html5lib")
    article = doc.find("div", id="article")
    if not article:
        return ""
    if comm_type == "statement":
        divs = article.find_all("div")
        return divs[2].get_text(" ", strip=True) if len(divs) > 2 else article.get_text(" ", strip=True)
    return article.get_text(" ", strip=True)


async def fetch_fomc_communications(since: datetime | None = None) -> List[FomcCommunication]:
    out: list[FomcCommunication] = []
    timeout = httpx.Timeout(30.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(FOMC_PAGE)
        resp.raise_for_status()
        doc = BeautifulSoup(resp.text, "html5lib")

        for panel in doc.select("div.panel.panel-default"):
            title = panel.select_one("div.panel-heading")
            year = re.findall(r"\d{4}", title.get_text(" ", strip=True))[0] if title else None
            if not year:
                continue

            for row in panel.select('div[class*="row fomc-meeting"]'):
                month_txt = row.select_one("div.fomc-meeting__month")
                day_txt = row.select_one("div.fomc-meeting__date")
                if not month_txt or not day_txt:
                    continue
                day_nums = re.findall(r"\d+", day_txt.get_text(" ", strip=True))
                if not day_nums:
                    continue
                meeting_dt = dt_parser.parse(f"{year} {month_txt.get_text(' ', strip=True).split('/')[-1]} {day_nums[-1]}").replace(tzinfo=timezone.utc)

                # Statement
                for div in row.find_all("div"):
                    txt = div.get_text(" ", strip=True)
                    if "Statement:" in txt or "Minutes:" in txt:
                        links = [a for a in div.find_all("a") if a.get_text(strip=True) == "HTML"]
                        if not links:
                            continue
                        url = BASE_URL + links[0].get("href", "")
                        page = await client.get(url)
                        if not page.is_success:
                            continue

                        ctype = "statement" if "Statement:" in txt else "minutes"
                        text = _extract_text_from_article(page.text, ctype)
                        release_dt = meeting_dt
                        if ctype == "minutes":
                            m = re.search(r"\(Released(.+?)\)", txt)
                            if m:
                                release_dt = dt_parser.parse(m.group(1).strip()).replace(tzinfo=timezone.utc)

                        if since and release_dt <= since:
                            continue

                        hawk, dove = _score_hawk_dove(text)
                        comm_id = _id_from_parts(ctype, release_dt.isoformat(), url)
                        out.append(
                            FomcCommunication(
                                comm_id=comm_id,
                                meeting_date=meeting_dt,
                                release_date=release_dt,
                                comm_type=ctype,
                                text=text,
                                source_url=url,
                                hawkish_score=hawk,
                                dovish_score=dove,
                            )
                        )
    return out
