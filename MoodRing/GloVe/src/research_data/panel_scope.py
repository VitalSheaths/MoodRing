"""Frozen 2020–2026 acquisition scope, separate from the first pilot archive."""

START = '2020-01-01'
END = '2026-11-03'
END_EXCLUSIVE = '2026-11-04'
MACRO_START = '2019-01-01'  # warm-up for trailing year-on-year comparisons
VERSION = 'ten-stock-panel-20261104-v1'
UNIVERSE = {
    'AAPL': ('Apple',), 'MSFT': ('Microsoft',), 'NVDA': ('Nvidia',),
    'GOOGL': ('Alphabet', 'Google'), 'AMZN': ('Amazon',),
    'META': ('Meta Platforms', 'Facebook'), 'TSLA': ('Tesla',),
    'AVGO': ('Broadcom',), 'LLY': ('Eli Lilly',), 'JPM': ('JPMorgan', 'JP Morgan'),
}
MACRO = {
    'DGS2': ('Percent', 'Daily', 'short-rate expectations'),
    'DGS10': ('Percent', 'Daily', 'long-term discount rates'),
    'FEDFUNDS': ('Percent', 'Monthly', 'policy-rate environment'),
    'CPIAUCSL': ('Index', 'Monthly', 'inflation'),
    'UNRATE': ('Percent', 'Monthly', 'labor-market slack'),
    'PAYEMS': ('Thousands of persons', 'Monthly', 'employment growth'),
    'ICSA': ('Number', 'Weekly', 'new unemployment claims'),
    'DCOILWTICO': ('USD per barrel', 'Daily', 'energy costs; negative values allowed'),
    'DTWEXBGS': ('Index', 'Daily', 'broad-dollar exposure'),
    'VIXCLS': ('Index', 'Daily', 'option-implied broad-market volatility'),
    'NFCI': ('Index', 'Weekly', 'financial conditions and stress'),
}
HORIZONS = (1, 5, 21, 63)  # sessions: next day, week, month, approximately quarter
MACRO_MAX_REQUESTS = 30
MACRO_MAX_PAGES_PER_SERIES = 8
MACRO_MAX_ROWS = 90_000
GDELT_MAX_ROWS = 50_000
GDELT_MAX_BYTES_BILLED = 10 * 1024**3
