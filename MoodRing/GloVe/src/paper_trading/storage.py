from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from paper_trading.schema import metadata


def make_engine(url: str):
    options = {'future': True, 'pool_pre_ping': True}
    if url.startswith('sqlite:'):
        options['connect_args'] = {'check_same_thread': False, 'timeout': 30}
        if url in ('sqlite://', 'sqlite:///:memory:'):
            options['poolclass'] = StaticPool
        elif url.startswith('sqlite:///'):
            Path(url.removeprefix('sqlite:///')).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **options)
    if engine.dialect.name == 'sqlite':
        @event.listens_for(engine, 'connect')
        def configure_sqlite(connection, record):
            connection.isolation_level = None
            connection.execute('PRAGMA foreign_keys=ON')

        @event.listens_for(engine, 'begin')
        def serialized_write(connection):
            # File-backed SQLite serializes ledger transactions across processes.
            connection.exec_driver_sql('BEGIN IMMEDIATE')
    return engine


class PaperStore:
    def __init__(self, engine):
        self.engine = engine
        metadata.create_all(engine)

    @contextmanager
    def transaction(self):
        with self.engine.begin() as connection:
            yield connection
