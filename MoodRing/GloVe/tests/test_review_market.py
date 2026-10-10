from fastapi.testclient import TestClient
import pytest

from api.server import create_app
from paper_trading.service import NoPriceDataError
from conftest import prediction


def test_provider_details_stay_in_server_logs(trading, tmp_path, caplog):
    service, clock, quotes, _ = trading
    scenario = service.create_scenario('alice', 'Errors')
    def fail(symbols):
        raise RuntimeError('private-provider-path /secret/database/password')
    quotes.get_quotes = fail
    with pytest.raises(NoPriceDataError, match='^Market data temporarily unavailable$'):
        service.publish_signal('alice', scenario['id'], 'AAPL', prediction(clock))
    with pytest.raises(NoPriceDataError, match='^Market data temporarily unavailable$'):
        service.refresh_prices('alice')
    app = create_app(f'sqlite:///{tmp_path / "api.sqlite"}', quotes, users={'token': 'alice'}, enable_checker=False)
    with TestClient(app) as client:
        response = client.get('/api/v1/market/quotes?tickers=AAPL', headers={'Authorization': 'Bearer token'})
        assert response.status_code == 503
        assert response.json()['detail'] == 'Market data temporarily unavailable'
        assert 'private-provider-path' not in response.text
        assert client.get('/health').json()['production_ready'] is False
        assert 'not production-ready' in client.get('/openapi.json').json()['info']['description']
    assert 'private-provider-path' in caplog.text
