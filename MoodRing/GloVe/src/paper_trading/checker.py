import asyncio
import logging

log = logging.getLogger(__name__)


class PriceChecker:
    def __init__(self, service, interval_s=300):
        if interval_s < 1:
            raise ValueError('Price check interval must be positive')
        self.service, self.interval_s = service, interval_s
        self.last_result, self.last_error = None, None

    async def check_pending_prices(self):
        self.last_result = await asyncio.to_thread(self.service.refresh_prices)
        self.last_error = None
        return self.last_result

    async def run(self):
        while True:
            try:
                await self.check_pending_prices()
            except Exception as exc:
                self.last_error = str(exc)
                log.exception('Price refresh failed; retaining the last recorded marks')
            await asyncio.sleep(self.interval_s)
