"""Conservative headline relevance rules; organization mentions alone never qualify.

These versioned English rules prioritize precision, not exhaustive coverage. Every
decision is provisional and preserves the source row for review. They do not use
price outcomes, held-out answers, article URLs as instructions, or remote services.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from html import unescape
import json
import re
import unicodedata

from .archive import digest
from .panel_scope import UNIVERSE

VERSION = 'headline-relevance-v2'
FINANCE = re.compile(
    r'\b(stock|stocks|share|shares|shareholders?|earnings|revenues?|profits?|'
    r'dividends?|investors?|investment|valuation|market cap|price target|'
    r'quarterly|sales|acquisition|merger|buyback|buybacks|billion|million|'
    r'analysts?|outlook|guidance|upgrade|downgrade|rally|rallies|surge|surges|'
    r'plunge|plunges|rise|fall|growth|growing|demand|forecast|forecasts|'
    r'antitrust|lawsuit|sues|suing|settlement|regulator|regulators|fines?|'
    r'tax|taxes|layoffs?|workers|employees|union|ceo|chief|business|'
    r'production|deliveries|delivery|factory|factories|manufacturing|'
    r'launch|launches|launched|patent|patents|researchers|research|'
    r'partners?|partnership|supporting|security|privacy|hackers|ban|bans)\b', re.I)
PRODUCT = {
    'AAPL': r'\b(iphone|ipad|macbook|airpods|ios|jailbreak|jailbreaks|apple watch|tim cook)\b',
    'MSFT': r'\b(azure|windows|xbox|office 365|cloud|satya nadella|connected cars)\b',
    'NVDA': r'\b(gpu|gpus|geforce|egx|graphics|servers?|chips?|semiconductors?)\b',
    'GOOGL': r'\b(ai|artificial intelligence|system|algorithm|algorithms|cloud|android|pixel|advertising|sundar pichai)\b',
    'AMZN': r'\b(aws|warehouse|warehouses|logistics|jeff bezos|cloud)\b',
    'META': r'\b(advertising|advertisers|privacy|data breach|mark zuckerberg)\b',
    'TSLA': r'\b(model 3|model s|model x|model y|cybertruck|gigafactory|autopilot|electric cars?|electric vehicles?|elon musk)\b',
    'AVGO': r'\b(chips?|semiconductors?|broadband|wireless)\b',
    'LLY': r'\b(drug|drugs|insulin|clinical|trial|trials|fda|pharma)\b',
    'JPM': r'\b(bank|banking|fintech|customer|customers|jamie dimon)\b',
}
AMBIGUOUS = {
    'AAPL': r'\b(apple (?:pie|cake|crumble|cider|juice|orchard|valley|creek|festival)|apples|recipe)\b',
    'AMZN': r'\b(amazon (?:rainforest|river|basin|jungle)|deforestation)\b',
    'TSLA': r'\b(nikola tesla|tesla coil|tesla museum)\b',
    'GOOGL': r'\b(alphabet (?:letters|song)|learn the alphabet)\b',
}
PROMOTIONAL = re.compile(
    r'\b(sale alert|coupon|coupons|promo code|best deals|black friday|'
    r'how to|how do|how can|gift guide|shopping guide|review:|'
    r'key players|market report|market research|market size|'
    r'fans build|drag racing|burn outs|pros and cons|vote here|'
    r'portable charger|travel charger|watch charger|active strap|'
    r'best-selling products|movie of the decade|an introduction|'
    r'what we.re reading|polls|reputation expert|seo content|'
    r'stocks on the move|stocks making big moves|top analyst upgrades)\b|'
    r'\b(?:market|markets)\s*[:\d]|\bdown to\b.*\$|'
    r'\b(?:deals?|save|spend|sales)\b.*\$|\b(?:best|new year|january)\b.*\bdeals\b|'
    r'\b(?:my favorite|you could|owner should|get this|swallowed|year.old got)\b', re.I)
INJECTION = re.compile(r'<script\b|ignore (?:all |previous |the )?instructions|'
                       r'system prompt|api keys?|developer message|execute (?:this |the )?command', re.I)


def clean_title(value: str | None) -> str:
    """Normalize text for matching without changing the archived headline."""
    text = value or ''
    for _ in range(2):
        text = unescape(text)
    return ' '.join(unicodedata.normalize('NFKC', text).split())


def title_candidates(title: str) -> list[str]:
    """Require a company name or exchange-qualified symbol in the headline."""
    return [ticker for ticker, names in UNIVERSE.items() if
            any(re.search(r'\b' + re.escape(name) + r'\b', title, re.I) for name in names)
            or re.search(r'\b(?:NASDAQ|NYSE)\s*:\s*' + ticker + r'\b', title, re.I)]


def relevance(title: str) -> dict[str, str]:
    """Classify each directly named target; uncertain matches remain review cases."""
    decisions = {}
    for ticker in title_candidates(title):
        if INJECTION.search(title):
            reason = 'review_instruction_like_text'
        elif ticker in AMBIGUOUS and re.search(AMBIGUOUS[ticker], title, re.I):
            reason = 'excluded_noncompany_name'
        elif PROMOTIONAL.search(title):
            reason = 'excluded_promotional_or_consumer_content'
        elif ticker == 'META' and re.search(
                r'\b(on facebook|facebook post|facebook live|facebook viral|posted to facebook)\b', title, re.I):
            reason = 'excluded_platform_used_by_others'
        elif ticker == 'AMZN' and re.search(r'\b(police|delivery driver|delivery truck)\b', title, re.I):
            reason = 'review_individual_delivery_incident'
        elif re.search(r'\b(?:not|rather than)\s+' + re.escape(UNIVERSE[ticker][0]) + r'\b', title, re.I):
            reason = 'review_negated_company_comparison'
        elif re.search(r'\b(?:cops|synagogue|ghosn|bernie sanders)\b', title, re.I):
            reason = 'review_incidental_person_or_product_story'
        elif ticker == 'JPM' and re.search(
                r'\b(conference|to present|to participate|asset management|underweight|overweight|'
                r'price target|upgrades?|downgrades?|rates?|rating|raises?|cuts?)\b', title, re.I):
            reason = 'review_broker_or_conference_role'
        elif re.search(r'\b(vs\.?|versus|compared|comparison)\b', title, re.I):
            reason = 'review_comparison_target'
        elif FINANCE.search(title) or re.search(PRODUCT[ticker], title, re.I):
            reason = 'accepted_direct_company_context'
        else:
            reason = 'review_insufficient_company_context'
        decisions[ticker] = reason
    return decisions


def english_language(title: str) -> tuple[str | None, float | None]:
    """Use seeded detection; short or uncertain headlines are never assumed English."""
    from langdetect import DetectorFactory, detect_langs, LangDetectException
    DetectorFactory.seed = 0
    # Brand-heavy mixed-script titles can fool short-text language detection.
    if any(c.isalpha() and 'LATIN' not in unicodedata.name(c, '') for c in title):
        return 'mixed_or_nonlatin', None
    if len(re.findall(r'\b\w+\b', title)) < 5:
        return None, None
    if not re.search(r'\b(the|a|an|and|of|to|for|with|from|its|is|are|will|'
                     r'could|may|can|has|have|says|now|stock|shares|earnings|sales)\b', title, re.I):
        return None, None
    try:
        result = detect_langs(title)[0]
        return result.lang, float(result.prob)
    except LangDetectException:
        return None, None


def identity_key(row):
    """Identify provider records independently of processing partitions."""
    return json.dumps([row.get('provider','gdelt_gkg'),row.get('gdelt_record_id') or
                       row.get('provider_item_id') or row['source_record_sha256']],separators=(',',':'))


def identity_signature(row):
    """Ignore query-specific Finnhub related-symbol metadata when comparing article versions."""
    return (digest(json.dumps({k:row.get(k) for k in ('title','url','published_at')},sort_keys=True).encode())
            if row.get('provider')=='finnhub' else row['source_record_sha256'])


def curate(rows: list[dict], language_detector=english_language,
           conflicting_identities=None, dedup_state=None) -> tuple[list[dict], list[dict]]:
    """Retain an audit and select the earliest headline in each seven-day text cluster.

Conflicting GKG record IDs are quarantined. Exact normalized headline copies,
including copies on different publishers, receive one vote. Similar but unequal
titles are not claimed to be deduplicated syndication clusters.
"""
    identities = {}
    for row in rows:
        identity = identity_key(row)
        # Finnhub's `related` field is query-specific. Compare article identity
        # fields for collisions, while retaining each complete source-row hash.
        identities.setdefault(identity, set()).add(identity_signature(row))
    audit, accepted = [], []
    language_cache = {}
    for index, row in enumerate(rows):
        title = clean_title(row['title'])
        decisions = relevance(title)
        identity = identity_key(row)
        proxy = row.get('alignment_proxy_at') or row.get('gdelt_seen_at')
        is_gdelt = row.get('provider','gdelt_gkg') == 'gdelt_gkg'
        lang, probability = None, None
        if not title:
            status = 'excluded_missing_title'
        elif len(identities[identity]) > 1 or identity in (conflicting_identities or ()):
            status = 'review_conflicting_record_id'
        elif not is_gdelt and row.get('date_status') != 'in_window':
            status = 'review_unknown_or_out_of_window_publication'
        elif not row.get('url') or not proxy:
            status = 'review_missing_url_or_observation_time'
        elif is_gdelt and row.get('published_at') and row['published_at'] > row['gdelt_seen_at']:
            status = 'review_publication_after_observation'
        elif is_gdelt and row.get('published_at') and (datetime.fromisoformat(row['gdelt_seen_at']) -
                datetime.fromisoformat(row['published_at'])) > timedelta(days=7):
            status = 'review_stale_publication'
        elif not decisions:
            status = 'excluded_metadata_only_mention'
        elif INJECTION.search(title):
            status = 'review_instruction_like_text'
        else:
            targets = [t for t, reason in decisions.items() if reason.startswith('accepted_')]
            if not targets:
                status = 'excluded_or_review_relevance'
            elif len(targets) > 1 or len(decisions) > 1:
                status = 'review_multiple_company_targets'
            else:
                if title not in language_cache:
                    language_cache[title] = language_detector(title)
                lang, probability = language_cache[title]
                status = ('eligible_for_headline_scoring' if lang == 'en' and
                          probability is not None and probability >= 0.90 else
                          'review_language_unsupported_or_uncertain')
        item = {'input_row': index + 1, 'source_record_sha256': row['source_record_sha256'],
                'gdelt_record_id': row.get('gdelt_record_id'), 'article_id': row['article_id'],
                'provider': row.get('provider','gdelt_gkg'),
                'title': title, 'url': row['url'], 'gdelt_seen_at': row.get('gdelt_seen_at'),
                'alignment_proxy_at': proxy,
                'timing_policy': row.get('timing_policy','gdelt_observation_not_publication'),
                'published_at': row['published_at'], 'ingested_at': row['ingested_at'],
                'title_tickers': list(decisions),
                'relevance_decisions_json': json.dumps(decisions, sort_keys=True),
                'language': lang, 'language_probability_uncalibrated': probability,
                'status': status, 'duplicate_of': None, 'relevance_version': VERSION,
                'human_approved': False, 'historical_prediction_eligible': False}
        for field in ('source_manifest_sha256','source_manifest_row'):
            if field in row:
                item[field]=row[field]
        audit.append(item)
        if status == 'eligible_for_headline_scoring':
            ticker = next(iter(decisions))
            accepted.append(dict(item, ticker=ticker,
                                 headline_cluster_id=digest((ticker+'|'+title.casefold()).encode())))
    accepted.sort(key=lambda r: (r['alignment_proxy_at'], r['source_record_sha256']))
    seen, unique = (dedup_state if dedup_state is not None else {}), []
    for item in accepted:
        key = item['headline_cluster_id']
        if key in seen and (datetime.fromisoformat(item['alignment_proxy_at'])-
                            datetime.fromisoformat(seen[key]['alignment_proxy_at'])) <= timedelta(days=7):
            original = audit[item['input_row']-1]
            original['status'] = 'excluded_duplicate_headline'
            original['duplicate_of'] = seen[key]['source_record_sha256']
        else:
            seen[key] = {'alignment_proxy_at':item['alignment_proxy_at'],
                         'source_record_sha256':item['source_record_sha256']}
            # The same text in another year is a new observation, not a global duplicate.
            item['headline_cluster_id'] = digest((key+'|'+item['alignment_proxy_at']).encode())
            unique.append(item)
    return audit, unique
