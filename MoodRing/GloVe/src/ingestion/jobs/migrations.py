"""Explicit, transactional migration; never invoked by application startup."""
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from ingestion.storage.postgres import metadata as legacy_metadata
from ingestion.universe.identity import content_hash
from .errors import StorageError

VERSION = '001_jobs'
DDL = Path(__file__).with_name('migrations').joinpath('001_jobs.sql').read_text()
TABLES = ('job_manifests', 'job_coordination', 'job_attempts', 'job_checkpoints',
          'job_artifacts', 'job_batches', 'job_revisions', 'job_record_appearances')
HISTORY = 'ingestion_job_migrations'


def checksum(dialect):
    # Hash semantic baseline attributes, not SQLAlchemy's formatting of CREATE TABLE.
    baseline = [(t.schema,t.name,[(c.name,type(c.type).__name__,getattr(c.type,'length',None),
                 getattr(c.type,'timezone',None),c.nullable,c.primary_key) for c in t.columns])
                for t in legacy_metadata.sorted_tables]
    return content_hash((VERSION,baseline,DDL))


def _fingerprint(conn):
    names = list(TABLES) + list(t.name for t in legacy_metadata.tables.values()) + [HISTORY]
    params = {'names': names}
    result = {}
    queries = {
        'columns': '''SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
            pg_get_expr(d.adbin,d.adrelid),a.attidentity
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname='bronze' AND c.relname=ANY(:names) AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname,a.attnum''',
        'constraints': '''SELECT c.relname,x.conname,x.contype,pg_get_constraintdef(x.oid)
            FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='bronze' AND c.relname=ANY(:names) ORDER BY c.relname,x.conname''',
        'indexes': '''SELECT tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname='bronze' AND tablename=ANY(:names) ORDER BY tablename,indexname''',
        'triggers': '''SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid)
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='bronze' AND c.relname=ANY(:names) AND NOT t.tgisinternal ORDER BY c.relname,t.tgname''',
        'functions': '''SELECT p.proname,pg_get_functiondef(p.oid) FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bronze' AND p.proname LIKE 'job_%'
            ORDER BY p.proname''',
    }
    for key, sql in queries.items():
        result[key] = [tuple(row) for row in conn.execute(text(sql), params)]
    return content_hash(result)


def _legacy_compatible(conn):
    inspector = inspect(conn)
    for table in legacy_metadata.tables.values():
        if not inspector.has_table(table.name, schema='bronze'):
            continue
        columns = {c['name']: c for c in inspector.get_columns(table.name, schema='bronze')}
        if set(columns) != {c.name for c in table.columns}:
            return False
        if inspector.get_pk_constraint(table.name, schema='bronze')['constrained_columns'] != [c.name for c in table.primary_key]:
            return False
        for expected in table.columns:
            actual = columns[expected.name]
            if actual['nullable'] != expected.nullable:
                return False
            if table.name == 'ingestion_runs' and expected.name == 'status':
                if not str(actual['type'].compile(dialect=conn.dialect)).startswith('VARCHAR(') or getattr(actual['type'], 'length', None) not in (16, 32):
                    return False
            elif table.name == 'ingestion_runs' and expected.name == 'run_id':
                if not str(actual['type'].compile(dialect=conn.dialect)).startswith('VARCHAR(') or getattr(actual['type'], 'length', None) not in (64, 100):
                    return False
            elif str(actual['type'].compile(dialect=conn.dialect)) != str(expected.type.compile(dialect=conn.dialect)):
                return False
    return True


def _status(conn):
    if conn.dialect.name != 'postgresql':
        raise StorageError('postgresql_required')
    conn.execute(text('SET LOCAL search_path=pg_catalog'))
    inspector = inspect(conn)
    tables = set(inspector.get_table_names(schema='bronze')) if inspector.has_schema('bronze') else set()
    jobs = {name for name in tables if name.startswith('job_')}
    if HISTORY not in tables:
        return 'pending' if not jobs and _legacy_compatible(conn) else 'incompatible'
    if jobs != set(TABLES):
        return 'incompatible'
    if {c['name'] for c in inspector.get_columns(HISTORY,schema='bronze')} != {'version','checksum','schema_sha','applied_at'}:
        return 'incompatible'
    history = conn.execute(text('SELECT version,checksum,schema_sha FROM bronze.ingestion_job_migrations')).mappings().all()
    if len(history) != 1 or history[0]['version'] != VERSION:
        return 'incompatible'
    if history[0]['checksum'] != checksum(conn.dialect):
        return 'checksum_mismatch'
    if history[0]['schema_sha'] != _fingerprint(conn):
        return 'incompatible'
    return 'applied'


def inspect_schema(engine):
    try:
        with engine.begin() as conn:
            return _status(conn)
    except SQLAlchemyError:
        raise StorageError('database_unavailable') from None


def migrate(engine):
    """Explicit caller authorization required; pass a disposable engine in tests."""
    try:
        with engine.begin() as conn:
            if conn.dialect.name != 'postgresql':
                raise StorageError('postgresql_required')
            conn.execute(text('SELECT pg_advisory_xact_lock(1200261019)'))
            status = _status(conn)
            if status == 'applied':
                return 'already_applied'
            if status != 'pending':
                raise StorageError('migration_checksum_mismatch' if status == 'checksum_mismatch' else 'schema_mismatch')
            conn.execute(text('CREATE SCHEMA IF NOT EXISTS bronze'))
            legacy_metadata.create_all(conn)
            conn.execute(text('''CREATE TABLE bronze.ingestion_job_migrations (
                version text PRIMARY KEY, checksum varchar(64) NOT NULL, schema_sha varchar(64) NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT clock_timestamp())'''))
            conn.exec_driver_sql(DDL)
            conn.execute(text('INSERT INTO bronze.ingestion_job_migrations(version,checksum,schema_sha) VALUES (:v,:c,:s)'),
                         {'v': VERSION, 'c': checksum(conn.dialect), 's': _fingerprint(conn)})
        return 'applied'
    except SQLAlchemyError as exc:
        code = getattr(getattr(exc,'orig',None),'pgcode',None)
        raise StorageError('database_unavailable' if code is None or code.startswith('08') else 'migration_failed') from None
