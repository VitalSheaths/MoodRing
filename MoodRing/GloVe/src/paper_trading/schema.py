"""Portable paper ledger, separate from the Postgres research schemas."""
from sqlalchemy import (
    JSON, Column, ForeignKey, Integer, MetaData, Numeric, String, Table,
    UniqueConstraint, CheckConstraint,
)

metadata = MetaData()
Money = Numeric(24, 8)
Quantity = Numeric(24, 10)

scenarios = Table(
    'paper_scenarios', metadata,
    Column('id', String(36), primary_key=True), Column('user_id', String(128), nullable=False, index=True),
    Column('name', String(120), nullable=False), Column('config_json', JSON, nullable=False),
    Column('research_json', JSON, nullable=False), Column('model_version', String(128), nullable=False),
    Column('dataset_version', String(128)), Column('feature_version', String(128)),
    Column('created_at', String(40), nullable=False),
)
portfolios = Table(
    'paper_portfolios', metadata,
    Column('id', String(36), primary_key=True),
    Column('scenario_id', ForeignKey('paper_scenarios.id'), nullable=False, unique=True),
    Column('user_id', String(128), nullable=False, index=True),
    Column('initial_capital', Money, nullable=False), Column('cash_balance', Money, nullable=False),
    Column('total_pnl', Money, nullable=False, default=0),
    Column('winning_trades', Integer, nullable=False, default=0),
    Column('closed_trades', Integer, nullable=False, default=0),
    Column('peak_equity', Money, nullable=False), Column('created_at', String(40), nullable=False),
    CheckConstraint('cash_balance >= 0', name='paper_cash_nonnegative'),
)
signals = Table(
    'paper_signals', metadata,
    Column('id', String(36), primary_key=True),
    Column('scenario_id', ForeignKey('paper_scenarios.id'), nullable=False, index=True),
    Column('user_id', String(128), nullable=False), Column('ticker', String(16), nullable=False),
    Column('stance', String(16), nullable=False), Column('prediction_json', JSON, nullable=False),
    Column('decision_json', JSON, nullable=False), Column('model_version', String(128), nullable=False),
    Column('feature_version', String(128)), Column('prediction_time', String(40), nullable=False),
    Column('expires_at', String(40), nullable=False), Column('entry_price', Money, nullable=False),
    Column('created_at', String(40), nullable=False),
)
positions = Table(
    'paper_positions', metadata,
    Column('id', String(36), primary_key=True),
    Column('portfolio_id', ForeignKey('paper_portfolios.id'), nullable=False, index=True),
    Column('ticker', String(16), nullable=False), Column('quantity', Quantity, nullable=False),
    Column('entry_price', Money, nullable=False), Column('cost_basis', Money, nullable=False),
    Column('mark_price', Money, nullable=False), Column('quote_at', String(40), nullable=False),
    Column('stop_loss_price', Money, nullable=False), Column('take_profit_price', Money, nullable=False),
    Column('status', String(16), nullable=False), Column('opened_at', String(40), nullable=False),
    Column('closed_at', String(40)),
    CheckConstraint('quantity > 0', name='paper_quantity_positive'),
)
trades = Table(
    'paper_trades', metadata,
    Column('id', String(36), primary_key=True),
    Column('position_id', ForeignKey('paper_positions.id'), nullable=False, unique=True),
    Column('portfolio_id', ForeignKey('paper_portfolios.id'), nullable=False, index=True),
    Column('signal_id', ForeignKey('paper_signals.id'), nullable=False),
    Column('user_id', String(128), nullable=False), Column('agent_id', String(128)),
    Column('entry_price', Money, nullable=False), Column('exit_price', Money),
    Column('pnl_dollars', Money), Column('pnl_pct', Money),
    Column('entry_fee', Money, nullable=False), Column('exit_fee', Money),
    Column('status', String(16), nullable=False), Column('opened_at', String(40), nullable=False),
    Column('closed_at', String(40)), Column('exit_reason', String(40)),
)
orders = Table(
    'paper_orders', metadata,
    Column('id', String(36), primary_key=True),
    Column('portfolio_id', ForeignKey('paper_portfolios.id'), nullable=False),
    Column('trade_id', ForeignKey('paper_trades.id'), nullable=False),
    Column('user_id', String(128), nullable=False), Column('idempotency_key', String(128), nullable=False),
    Column('request_json', JSON, nullable=False), Column('side', String(4), nullable=False),
    Column('quantity', Quantity, nullable=False), Column('status', String(16), nullable=False),
    Column('created_at', String(40), nullable=False),
    UniqueConstraint('user_id', 'idempotency_key', name='paper_order_idempotency'),
)
fills = Table(
    'paper_fills', metadata,
    Column('id', String(36), primary_key=True),
    Column('order_id', ForeignKey('paper_orders.id'), nullable=False, unique=True),
    Column('quantity', Quantity, nullable=False), Column('price', Money, nullable=False),
    Column('fee', Money, nullable=False), Column('quote_price', Money, nullable=False),
    Column('quote_at', String(40), nullable=False), Column('source', String(40), nullable=False),
    Column('filled_at', String(40), nullable=False),
)
history = Table(
    'paper_equity_history', metadata,
    Column('id', String(36), primary_key=True),
    Column('portfolio_id', ForeignKey('paper_portfolios.id'), nullable=False, index=True),
    Column('recorded_at', String(40), nullable=False), Column('event', String(24), nullable=False),
    Column('cash_balance', Money, nullable=False), Column('market_value', Money, nullable=False),
    Column('equity', Money, nullable=False), Column('realized_pnl', Money, nullable=False),
    Column('unrealized_pnl', Money, nullable=False), Column('drawdown', Money, nullable=False),
)
quotes = Table(
    'paper_quotes', metadata,
    Column('ticker', String(16), primary_key=True), Column('price', Money, nullable=False),
    Column('quote_at', String(40), nullable=False), Column('fetched_at', String(40), nullable=False),
    Column('source', String(40), nullable=False),
)
performance = Table(
    'signal_performance', metadata,
    Column('signal_id', ForeignKey('paper_signals.id'), primary_key=True),
    Column('price_1h', Money), Column('price_4h', Money), Column('price_1d', Money), Column('price_1w', Money),
    Column('max_gain_pct', Money, nullable=False, default=0),
    Column('max_loss_pct', Money, nullable=False, default=0), Column('updated_at', String(40), nullable=False),
)

backtest_runs = Table(
    'backtest_runs', metadata,
    Column('id', String(36), primary_key=True), Column('user_id', String(128), nullable=False, index=True),
    Column('model_version', String(128), nullable=False), Column('dataset_version', String(128)),
    Column('feature_version', String(128)), Column('config_json', JSON, nullable=False),
    Column('research_json', JSON, nullable=False), Column('result_json', JSON, nullable=False),
    Column('risk_json', JSON, nullable=False), Column('created_at', String(40), nullable=False),
)
