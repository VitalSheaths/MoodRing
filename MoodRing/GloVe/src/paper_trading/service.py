"""Atomic long-stock simulation. All quantities, fees and cash use Decimal."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
import threading
import logging
from functools import wraps
from uuid import uuid4

from sqlalchemy import and_, insert, select, update
from sqlalchemy.exc import IntegrityError

from engine.decision_engine import DecisionEngine
from paper_trading.market import Quote, timestamp, ticker_symbol, utcnow
from paper_trading.policy import ScenarioPolicy, decimal, money
from paper_trading import schema as db


logger = logging.getLogger(__name__)


class PaperTradingError(ValueError):
    status_code = 400


class NotFoundError(PaperTradingError):
    status_code = 404


class NotOwnerError(PaperTradingError):
    status_code = 403


class NoPriceDataError(PaperTradingError):
    status_code = 503


class InsufficientBalanceError(PaperTradingError):
    pass


class TradeAlreadyClosedError(PaperTradingError):
    status_code = 409


class IdempotencyConflictError(PaperTradingError):
    status_code = 409


def idempotent_mutation(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except IntegrityError as exc:
            message = str(exc.orig)
            if ('paper_order_idempotency' in message
                    or 'paper_orders.user_id, paper_orders.idempotency_key' in message):
                raise IdempotencyConflictError('Idempotency key is already committed; retry the original request') from exc
            raise
    return wrapped


def new_id():
    return str(uuid4())


class PaperTradingService:
    def __init__(self, store, quote_provider, clock=utcnow):
        self.store, self.quote_provider, self.clock = store, quote_provider, clock
        self.lock = threading.RLock()

    def _owned(self, connection, table, identity, user_id):
        row = connection.execute(select(table).where(table.c.id == identity)).mappings().first()
        if row is None:
            raise NotFoundError(f'{table.name} record not found')
        if row['user_id'] != user_id:
            raise NotOwnerError('Record belongs to another user')
        return dict(row)

    def _portfolio(self, connection, scenario_id, user_id):
        self._owned(connection, db.scenarios, scenario_id, user_id)
        row = connection.execute(select(db.portfolios).where(
            db.portfolios.c.scenario_id == scenario_id).with_for_update()).mappings().one()
        return dict(row)

    def _policy(self, connection, scenario_id):
        config = connection.execute(select(db.scenarios.c.config_json).where(
            db.scenarios.c.id == scenario_id)).scalar_one()
        return ScenarioPolicy(**config)

    def _quote(self, symbol, policy):
        try:
            quote = self.quote_provider.get_quotes([symbol]).get(symbol)
            if quote is None:
                raise ValueError(f'No price data for {symbol}')
            if quote.ticker != symbol:
                raise ValueError('Provider returned a mismatched ticker')
            return quote.require_fresh(self.clock(), policy.quote_max_age_s)
        except Exception as exc:
            logger.warning('Market data validation or lookup failed', exc_info=True)
            raise NoPriceDataError('Market data temporarily unavailable') from exc

    def _save_quote(self, connection, quote):
        dialect = connection.dialect.name
        if dialect == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert as upsert
        elif dialect == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert as upsert
        else:
            raise ValueError('Paper trading supports SQLite and PostgreSQL')
        values = dict(ticker=quote.ticker, price=quote.price, quote_at=quote.quote_at.isoformat(),
                      fetched_at=quote.fetched_at.isoformat(), source=quote.source)
        stmt = upsert(db.quotes).values(**values)
        connection.execute(stmt.on_conflict_do_update(index_elements=['ticker'], set_=values,
                           where=db.quotes.c.quote_at <= stmt.excluded.quote_at))

    def create_scenario(self, user_id, name, initial_capital=10000, config=None,
                        model_version='manual-v1', dataset_version=None, feature_version=None, research=None):
        policy = ScenarioPolicy(**(config or {}))
        try:
            policy.fraction(0.5)  # Validate Kelly prerequisites before storing the scenario.
        except ValueError as exc:
            raise PaperTradingError(str(exc)) from exc
        capital = money(initial_capital)
        if capital <= 0:
            raise PaperTradingError('Initial capital must be positive')
        scenario_id, portfolio_id, now = new_id(), new_id(), self.clock().isoformat()
        with self.lock, self.store.transaction() as connection:
            connection.execute(insert(db.scenarios).values(
                id=scenario_id, user_id=user_id, name=name, config_json=policy.model_dump(),
                research_json=research or {}, model_version=model_version,
                dataset_version=dataset_version, feature_version=feature_version, created_at=now))
            connection.execute(insert(db.portfolios).values(
                id=portfolio_id, scenario_id=scenario_id, user_id=user_id, initial_capital=capital,
                cash_balance=capital, total_pnl=0, winning_trades=0, closed_trades=0,
                peak_equity=capital, created_at=now))
            portfolio = self._portfolio(connection, scenario_id, user_id)
            self._record_snapshot(connection, portfolio, 'created')
        return self.get_scenario(user_id, scenario_id)

    def _snapshot(self, connection, portfolio):
        policy = self._policy(connection, portfolio['scenario_id'])
        rows = connection.execute(select(db.positions).where(and_(
            db.positions.c.portfolio_id == portfolio['id'], db.positions.c.status == 'open'
        )).order_by(db.positions.c.opened_at, db.positions.c.id)).mappings().all()
        market_value, cost_basis, out = Decimal(0), Decimal(0), []
        for row in rows:
            position = dict(row)
            value = money(row['quantity'] * row['mark_price'])
            position['market_value'] = value
            position['unrealized_pnl'] = money(value - row['cost_basis'])
            age = (self.clock() - timestamp(row['quote_at'])).total_seconds()
            position['quote_is_stale'] = age < 0 or age > policy.quote_max_age_s
            market_value += value
            cost_basis += row['cost_basis']
            out.append(position)
        equity = money(portfolio['cash_balance'] + market_value)
        peak = max(portfolio['peak_equity'], equity)
        return {
            **portfolio, 'market_value': market_value, 'equity': equity,
            'realized_pnl': portfolio['total_pnl'], 'unrealized_pnl': money(market_value - cost_basis),
            'total_return_pct': (equity / portfolio['initial_capital'] - 1) * 100,
            'gross_exposure_pct': market_value / equity * 100 if equity > 0 else Decimal(0),
            'drawdown': max(Decimal(0), (peak - equity) / peak), 'peak_equity': peak,
            'positions': out, 'valuation_is_stale': any(p['quote_is_stale'] for p in out),
        }

    def _record_snapshot(self, connection, portfolio, event):
        state = self._snapshot(connection, portfolio)
        connection.execute(update(db.portfolios).where(db.portfolios.c.id == portfolio['id']).values(
            peak_equity=state['peak_equity']))
        connection.execute(insert(db.history).values(
            id=new_id(), portfolio_id=portfolio['id'], recorded_at=self.clock().isoformat(), event=event,
            **{k: state[k] for k in ('cash_balance', 'market_value', 'equity', 'realized_pnl',
                                    'unrealized_pnl', 'drawdown')}))
        return state

    def get_scenario(self, user_id, scenario_id):
        with self.lock, self.store.transaction() as connection:
            scenario = self._owned(connection, db.scenarios, scenario_id, user_id)
            scenario['portfolio'] = self._snapshot(connection, self._portfolio(connection, scenario_id, user_id))
            return scenario

    def list_scenarios(self, user_id):
        with self.lock, self.store.transaction() as connection:
            ids = connection.execute(select(db.scenarios.c.id).where(
                db.scenarios.c.user_id == user_id).order_by(db.scenarios.c.created_at, db.scenarios.c.id)).scalars().all()
        return [self.get_scenario(user_id, identity) for identity in ids]

    def _decision(self, prediction, quote, policy):
        now = self.clock()
        try:
            cutoff, available_at = timestamp(prediction['prediction_time']), timestamp(prediction['feature_available_at'])
        except ValueError as exc:
            raise PaperTradingError(str(exc)) from exc
        if available_at > cutoff or cutoff > now:
            raise PaperTradingError('Features must be available at or before prediction time, which cannot be in the future')
        if (now - cutoff).total_seconds() > policy.signal_max_age_s:
            raise PaperTradingError('Prediction has expired')
        metadata = {'hours_since_update': (now - available_at).total_seconds() / 3600,
                    'missing_ratio': prediction.get('missing_ratio', 0), 'top_drivers': prediction.get('top_drivers', [])}
        engine = DecisionEngine(stale_threshold_hours=policy.feature_max_age_s / 3600,
                                commission_bps=policy.commission_bps, slippage_bps=policy.slippage_bps)
        return engine.evaluate_trade_decision(
            ticker=quote.ticker, prob_up=prediction['prob_up'], mu=prediction['mu'], var=prediction['var'],
            trade_success_prob=prediction['trade_success_prob'], drawdown_risk=prediction['drawdown_risk'],
            regime_multiplier=prediction.get('regime_multiplier', 1),
            dominant_regime=prediction.get('market_regime', 'unknown'), current_price=float(quote.price),
            feature_metadata=metadata,
            estimated_cost_drag=2 * (policy.commission_bps + policy.slippage_bps) / 10000)

    def publish_signal(self, user_id, scenario_id, ticker, prediction):
        try:
            ticker = ticker_symbol(ticker)
        except ValueError as exc:
            raise PaperTradingError(str(exc)) from exc
        with self.lock, self.store.transaction() as connection:
            scenario = self._owned(connection, db.scenarios, scenario_id, user_id)
            policy = ScenarioPolicy(**scenario['config_json'])
        quote = self._quote(ticker, policy)
        decision = self._decision(prediction, quote, policy)
        identity, now = new_id(), self.clock()
        with self.lock, self.store.transaction() as connection:
            connection.execute(insert(db.signals).values(
                id=identity, user_id=user_id, scenario_id=scenario_id, ticker=ticker, stance='bullish',
                prediction_json=prediction, decision_json=decision, model_version=scenario['model_version'],
                feature_version=scenario['feature_version'], prediction_time=prediction['prediction_time'],
                expires_at=(timestamp(prediction['prediction_time']) + timedelta(seconds=policy.signal_max_age_s)).isoformat(),
                entry_price=quote.price, created_at=now.isoformat()))
            connection.execute(insert(db.performance).values(
                signal_id=identity, max_gain_pct=0, max_loss_pct=0, updated_at=now.isoformat()))
            self._save_quote(connection, quote)
            return dict(connection.execute(select(db.signals).where(db.signals.c.id == identity)).mappings().one())

    def _replay(self, connection, user_id, key, request):
        row = connection.execute(select(db.orders).where(and_(db.orders.c.user_id == user_id,
                    db.orders.c.idempotency_key == key))).mappings().first()
        if row:
            if row['request_json'] != request:
                raise IdempotencyConflictError('Idempotency key has already been used for a different request')
            return self._trade(connection, row['trade_id'], user_id)
        return None

    def _trade(self, connection, trade_id, user_id):
        trade = self._owned(connection, db.trades, trade_id, user_id)
        trade['position'] = dict(connection.execute(select(db.positions).where(
            db.positions.c.id == trade['position_id'])).mappings().one())
        return trade

    def _fill(self, connection, *, portfolio, trade_id, key, request, side, quantity, price, fee, quote):
        order_id = new_id()
        now = self.clock().isoformat()
        connection.execute(insert(db.orders).values(
            id=order_id, portfolio_id=portfolio['id'], trade_id=trade_id, user_id=portfolio['user_id'],
            idempotency_key=key, request_json=request, side=side, quantity=quantity, status='filled', created_at=now))
        connection.execute(insert(db.fills).values(
            id=new_id(), order_id=order_id, quantity=quantity, price=price, fee=fee,
            quote_price=quote.price, quote_at=quote.quote_at.isoformat(), source=quote.source, filled_at=now))

    @idempotent_mutation
    def execute_trade(self, user_id, signal_id, dollars, idempotency_key, agent_id=None):
        if not idempotency_key or len(idempotency_key) > 128:
            raise PaperTradingError('A 1–128 character idempotency key is required')
        requested = money(dollars) if dollars is not None else None
        if requested is not None and requested <= 0:
            raise PaperTradingError('Trade dollars must be positive')
        request = {'action': 'buy', 'signal_id': signal_id, 'dollars': str(requested) if requested is not None else None,
                   'agent_id': agent_id}
        with self.lock, self.store.transaction() as connection:
            replay = self._replay(connection, user_id, idempotency_key, request)
            if replay:
                return replay
            signal = self._owned(connection, db.signals, signal_id, user_id)
            policy = self._policy(connection, signal['scenario_id'])
        quote = self._quote(signal['ticker'], policy)
        with self.lock, self.store.transaction() as connection:
            portfolio = self._portfolio(connection, signal['scenario_id'], user_id)
            replay = self._replay(connection, user_id, idempotency_key, request)
            if replay:
                return replay
            now = self.clock()
            try:
                quote.require_fresh(now, policy.quote_max_age_s)
            except ValueError as exc:
                logger.warning('Market data validation failed', exc_info=True)
                raise NoPriceDataError('Market data temporarily unavailable') from exc
            if timestamp(signal['expires_at']) < now:
                raise PaperTradingError('Signal has expired')
            latest_at = connection.execute(select(db.quotes.c.quote_at).where(
                db.quotes.c.ticker == quote.ticker)).scalar_one_or_none()
            if latest_at is not None and quote.quote_at < timestamp(latest_at):
                raise NoPriceDataError('Entry quote is older than the last recorded observation')
            decision = self._decision(signal['prediction_json'], quote, policy)
            if decision['action'] != 'BUY':
                raise PaperTradingError(f"Signal is not tradeable: {decision.get('reason', 'PASS')}")
            state = self._snapshot(connection, portfolio)
            if state['valuation_is_stale']:
                raise NoPriceDataError('Refresh all open positions before sizing a new entry')
            if any(p['ticker'] == signal['ticker'] for p in state['positions']):
                raise PaperTradingError('This scenario already has an open position in this ticker')
            # Prevent retries with a different key from duplicating a signal after its position exits.
            used = connection.execute(select(db.trades.c.id).where(db.trades.c.signal_id == signal_id)).first()
            if used:
                raise PaperTradingError('Signal has already been traded in this scenario')
            fill_price = (quote.price * (1 + decimal(policy.slippage_bps) / 10000)).quantize(Decimal('0.00000001'))
            stop_price = decimal(decision['stop_loss_price'])
            stop_fraction = max(Decimal('0.000001'), (fill_price - stop_price) / fill_price)
            try:
                cap = policy.budget(equity=state['equity'], cash=state['cash_balance'], exposure=state['market_value'],
                    drawdown=state['drawdown'], confidence=decision['confidence'],
                    prediction_drawdown=decimal(signal['prediction_json']['drawdown_risk']), stop_fraction=stop_fraction)
            except ValueError as exc:
                raise PaperTradingError(str(exc)) from exc
            cap = min(cap, money(decision['position_size']))
            if requested is not None and requested > portfolio['cash_balance']:
                raise InsufficientBalanceError('Insufficient available cash')
            if requested is not None and requested > cap:
                raise PaperTradingError(f'Trade exceeds scenario risk budget; maximum is ${cap}')
            budget = requested if requested is not None else cap
            fee_rate = decimal(policy.commission_bps) / 10000
            quantity = (budget / (1 + fee_rate) / fill_price).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
            notional = money(quantity * fill_price)
            fee = money(notional * fee_rate)
            if notional + fee > budget:
                # Reserve one cent for independent fee and notional rounding.
                quantity = ((budget - Decimal('0.01')) / (1 + fee_rate) / fill_price).quantize(
                    Decimal('0.00000001'), rounding=ROUND_DOWN)
                notional, fee = money(quantity * fill_price), money(quantity * fill_price * fee_rate)
            cost = notional + fee
            if quantity <= 0 or cost <= 0 or cost > portfolio['cash_balance']:
                raise InsufficientBalanceError('Allocation is too small or exceeds available cash')
            trade_id, position_id = new_id(), new_id()
            connection.execute(insert(db.positions).values(
                id=position_id, portfolio_id=portfolio['id'], ticker=signal['ticker'], quantity=quantity,
                entry_price=fill_price, cost_basis=cost, mark_price=quote.price, quote_at=quote.quote_at.isoformat(),
                stop_loss_price=stop_price, take_profit_price=decimal(decision['take_profit_price']),
                status='open', opened_at=now.isoformat()))
            connection.execute(insert(db.trades).values(
                id=trade_id, position_id=position_id, portfolio_id=portfolio['id'], signal_id=signal_id,
                user_id=user_id, agent_id=agent_id, entry_price=fill_price, entry_fee=fee,
                status='open', opened_at=now.isoformat()))
            portfolio['cash_balance'] -= cost
            connection.execute(update(db.portfolios).where(db.portfolios.c.id == portfolio['id']).values(
                cash_balance=portfolio['cash_balance']))
            self._fill(connection, portfolio=portfolio, trade_id=trade_id, key=idempotency_key, request=request,
                       side='buy', quantity=quantity, price=fill_price, fee=fee, quote=quote)
            self._save_quote(connection, quote)
            self._record_snapshot(connection, portfolio, 'entry')
            return self._trade(connection, trade_id, user_id)

    @idempotent_mutation
    def close_trade(self, user_id, trade_id, idempotency_key, quote=None, exit_reason='manual'):
        if not idempotency_key or len(idempotency_key) > 128:
            raise PaperTradingError('A 1–128 character idempotency key is required')
        request = {'action': 'sell', 'trade_id': trade_id}
        with self.lock, self.store.transaction() as connection:
            replay = self._replay(connection, user_id, idempotency_key, request)
            if replay:
                return replay
            trade = self._trade(connection, trade_id, user_id)
            if trade['status'] != 'open':
                raise TradeAlreadyClosedError('Trade is already closed')
            portfolio_row = connection.execute(select(db.portfolios).where(
                db.portfolios.c.id == trade['portfolio_id'])).mappings().one()
            scenario_id = portfolio_row['scenario_id']
            policy = self._policy(connection, scenario_id)
        quote = quote or self._quote(trade['position']['ticker'], policy)
        with self.lock, self.store.transaction() as connection:
            portfolio = self._portfolio(connection, scenario_id, user_id)
            replay = self._replay(connection, user_id, idempotency_key, request)
            if replay:
                return replay
            trade = self._trade(connection, trade_id, user_id)
            if trade['status'] != 'open':
                raise TradeAlreadyClosedError('Trade is already closed')
            position = trade['position']
            try:
                quote.require_fresh(self.clock(), policy.quote_max_age_s)
                if quote.ticker != position['ticker'] or quote.quote_at < timestamp(position['quote_at']):
                    raise ValueError('Exit quote does not match the position or is out of order')
            except ValueError as exc:
                logger.warning('Market data validation failed', exc_info=True)
                raise NoPriceDataError('Market data temporarily unavailable') from exc
            price = (quote.price * (1 - decimal(policy.slippage_bps) / 10000)).quantize(Decimal('0.00000001'))
            gross = money(price * position['quantity'])
            fee = money(gross * decimal(policy.commission_bps) / 10000)
            proceeds = gross - fee
            pnl = money(proceeds - position['cost_basis'])
            now = self.clock().isoformat()
            connection.execute(update(db.positions).where(db.positions.c.id == position['id']).values(
                status='closed', closed_at=now, mark_price=quote.price, quote_at=quote.quote_at.isoformat()))
            connection.execute(update(db.trades).where(db.trades.c.id == trade_id).values(
                status='closed', closed_at=now, exit_price=price, exit_fee=fee,
                pnl_dollars=pnl, pnl_pct=pnl / position['cost_basis'] * 100, exit_reason=exit_reason))
            portfolio['cash_balance'] += proceeds
            portfolio['total_pnl'] += pnl
            portfolio['closed_trades'] += 1
            portfolio['winning_trades'] += int(pnl > 0)
            connection.execute(update(db.portfolios).where(db.portfolios.c.id == portfolio['id']).values(
                **{k: portfolio[k] for k in ('cash_balance', 'total_pnl', 'closed_trades', 'winning_trades')}))
            self._fill(connection, portfolio=portfolio, trade_id=trade_id, key=idempotency_key, request=request,
                       side='sell', quantity=position['quantity'], price=price, fee=fee, quote=quote)
            self._save_quote(connection, quote)
            self._record_snapshot(connection, portfolio, 'exit')
            return self._trade(connection, trade_id, user_id)

    def scenario_history(self, user_id, scenario_id, limit=500):
        with self.lock, self.store.transaction() as connection:
            portfolio = self._portfolio(connection, scenario_id, user_id)
            result = {}
            for name, table in [('trades', db.trades), ('orders', db.orders), ('equity', db.history)]:
                time_column = table.c.recorded_at if name == 'equity' else table.c.opened_at if name == 'trades' else table.c.created_at
                rows = connection.execute(select(table).where(table.c.portfolio_id == portfolio['id']).order_by(
                    time_column.desc(), table.c.id.desc()).limit(limit)).mappings().all()
                result[name] = [dict(row) for row in rows]
            order_ids = [row['id'] for row in result['orders']]
            result['fills'] = [dict(row) for row in connection.execute(select(db.fills).where(
                db.fills.c.order_id.in_(order_ids)).order_by(db.fills.c.filled_at, db.fills.c.id)).mappings()]
            result['signals'] = [dict(row) for row in connection.execute(select(db.signals).where(
                db.signals.c.scenario_id == scenario_id).order_by(db.signals.c.created_at.desc()).limit(limit)).mappings()]
            return result

    def compare(self, user_id, scenario_ids):
        if len(set(scenario_ids)) != len(scenario_ids) or not 2 <= len(scenario_ids) <= 20:
            raise PaperTradingError('Compare between 2 and 20 distinct scenarios')
        result = []
        for identity in scenario_ids:
            scenario = self.get_scenario(user_id, identity)
            state = scenario['portfolio']
            result.append({'scenario_id': identity, 'name': scenario['name'], 'model_version': scenario['model_version'],
                'dataset_version': scenario['dataset_version'], 'feature_version': scenario['feature_version'],
                'config': scenario['config_json'], 'research': scenario['research_json'],
                **{k: state[k] for k in ('initial_capital', 'equity', 'total_return_pct', 'realized_pnl',
                    'unrealized_pnl', 'gross_exposure_pct', 'drawdown', 'closed_trades', 'winning_trades', 'valuation_is_stale')}})
        return result

    def refresh_prices(self, user_id=None):
        """Batch updates for every watched ticker. Network failure leaves ledger state intact."""
        with self.lock, self.store.transaction() as connection:
            portfolio_query = select(db.portfolios)
            signal_query = select(db.signals).where(db.signals.c.created_at >= (
                self.clock() - timedelta(days=8)).isoformat())
            if user_id is not None:
                portfolio_query = portfolio_query.where(db.portfolios.c.user_id == user_id)
                signal_query = signal_query.where(db.signals.c.user_id == user_id)
            portfolios = [dict(row) for row in connection.execute(portfolio_query).mappings()]
            watched_signals = [dict(row) for row in connection.execute(signal_query).mappings()]
            portfolio_ids = [row['id'] for row in portfolios]
            watched_positions = connection.execute(select(db.positions).where(and_(
                db.positions.c.portfolio_id.in_(portfolio_ids), db.positions.c.status == 'open'))).mappings().all()
            symbols = sorted({row['ticker'] for row in watched_positions} | {row['ticker'] for row in watched_signals})
        try:
            fetched = self.quote_provider.get_quotes(symbols)
        except Exception as exc:
            logger.warning('Quote refresh failed', exc_info=True)
            raise NoPriceDataError('Market data temporarily unavailable') from exc
        valid = {}
        stale_or_missing = []
        for symbol in symbols:
            quote = fetched.get(symbol)
            try:
                if quote is None or quote.ticker != symbol:
                    raise ValueError('Missing or mismatched quote')
                quote.require_fresh(self.clock(), 3600)
                valid[symbol] = quote
            except ValueError:
                stale_or_missing.append(symbol)
        exits, updated = [], 0
        for old_portfolio in portfolios:
            with self.lock, self.store.transaction() as connection:
                portfolio = self._portfolio(connection, old_portfolio['scenario_id'], old_portfolio['user_id'])
                policy = self._policy(connection, portfolio['scenario_id'])
                rows = connection.execute(select(db.positions).where(and_(
                    db.positions.c.portfolio_id == portfolio['id'], db.positions.c.status == 'open'))).mappings().all()
                changed = False
                for position in rows:
                    quote = valid.get(position['ticker'])
                    if quote is None:
                        continue
                    try:
                        quote.require_fresh(self.clock(), policy.quote_max_age_s)
                    except ValueError:
                        stale_or_missing.append(position['ticker'])
                        continue
                    if quote.quote_at < timestamp(position['quote_at']):
                        continue
                    if quote.quote_at > timestamp(position['quote_at']) or quote.price != position['mark_price']:
                        connection.execute(update(db.positions).where(db.positions.c.id == position['id']).values(
                            mark_price=quote.price, quote_at=quote.quote_at.isoformat()))
                        self._save_quote(connection, quote)
                        updated += 1
                        changed = True
                    if policy.auto_exit and (quote.price <= position['stop_loss_price'] or quote.price >= position['take_profit_price']):
                        trade_id = connection.execute(select(db.trades.c.id).where(
                            db.trades.c.position_id == position['id'])).scalar_one()
                        reason = 'stop_loss' if quote.price <= position['stop_loss_price'] else 'take_profit'
                        exits.append((portfolio['user_id'], trade_id, quote, reason))
                if changed:
                    self._record_snapshot(connection, portfolio, 'mark')
        for signal in watched_signals:
            quote = valid.get(signal['ticker'])
            if quote is None or quote.quote_at < timestamp(signal['created_at']):
                continue
            with self.lock, self.store.transaction() as connection:
                row = connection.execute(select(db.performance).where(
                    db.performance.c.signal_id == signal['id']).with_for_update()).mappings().one()
                if quote.quote_at < timestamp(row['updated_at']):
                    continue
                elapsed = (quote.quote_at - timestamp(signal['created_at'])).total_seconds()
                gain = (quote.price / signal['entry_price'] - 1) * 100
                if signal['stance'] == 'bearish':
                    gain = -gain
                values = {'updated_at': quote.quote_at.isoformat(),
                    'max_gain_pct': max(row['max_gain_pct'], gain, Decimal(0)),
                    'max_loss_pct': min(row['max_loss_pct'], gain, Decimal(0))}
                for column, seconds in [('price_1h', 3600), ('price_4h', 14400), ('price_1d', 86400), ('price_1w', 604800)]:
                    if elapsed >= seconds and row[column] is None:
                        values[column] = quote.price
                connection.execute(update(db.performance).where(db.performance.c.signal_id == signal['id']).values(**values))
        closed = 0
        for owner, trade_id, quote, reason in exits:
            try:
                self.close_trade(owner, trade_id, f'auto:{trade_id}', quote=quote, exit_reason=reason)
                closed += 1
            except TradeAlreadyClosedError:
                continue
        return {'positions_updated': updated, 'positions_closed': closed, 'tickers_requested': symbols,
                'stale_or_missing': sorted(set(stale_or_missing))}

    def signal_history(self, user_id, signal_id):
        with self.lock, self.store.transaction() as connection:
            signal = self._owned(connection, db.signals, signal_id, user_id)
            signal['performance'] = dict(connection.execute(select(db.performance).where(
                db.performance.c.signal_id == signal_id)).mappings().one())
            return signal

    def save_backtest(self, user_id, result, dataset_version=None, feature_version=None):
        from fastapi.encoders import jsonable_encoder
        identity = new_id()
        curve = result['result_json']['equity_curve']
        risk = {'max_drawdown': max((decimal(row['drawdown']) for row in curve), default=Decimal(0))}
        values = dict(id=identity, user_id=user_id, model_version=result['model_version'],
            dataset_version=dataset_version, feature_version=feature_version,
            config_json=result['config_json'], research_json=result['research_json'],
            result_json=jsonable_encoder(result['result_json']), risk_json=jsonable_encoder(risk),
            created_at=self.clock().isoformat())
        with self.lock, self.store.transaction() as connection:
            connection.execute(insert(db.backtest_runs).values(**values))
        return values

    def list_backtests(self, user_id, limit=100):
        with self.lock, self.store.transaction() as connection:
            return [dict(row) for row in connection.execute(select(db.backtest_runs).where(
                db.backtest_runs.c.user_id == user_id).order_by(db.backtest_runs.c.created_at.desc()).limit(limit)).mappings()]

    def compare_backtests(self, user_id, run_ids):
        if len(set(run_ids)) != len(run_ids) or not 2 <= len(run_ids) <= 20:
            raise PaperTradingError('Compare between 2 and 20 distinct runs')
        with self.lock, self.store.transaction() as connection:
            return [self._owned(connection, db.backtest_runs, identity, user_id) for identity in run_ids]
