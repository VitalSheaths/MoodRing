"""Manifest-bound Parquet publication and verified in-memory inspection.

An exclusive directory and a manifest written last form the publication boundary.
Incomplete directories are not datasets. Readers bind to verified bytes, never
reopen a pathname in DuckDB, and reject symlinks in every path component. This is
local integrity checking, not protection from an owner rewriting all evidence.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat

import pyarrow as pa
import pyarrow.parquet as pq

from .scope import MAX_FILE, MAX_SNAPSHOT


def digest(data: bytes) -> str:
    """Identify exact archived bytes rather than a mutable pathname."""
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    """Encode deterministic metadata and reject NaN/Infinity."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2).encode()


def read_bytes(path: Path, limit: int = MAX_FILE) -> bytes:
    """Open each component without symlinks; bound and inspect the same file handle."""
    absolute = Path(os.path.abspath(path))
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        fd = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('archive_file_bound')
            data = source.read(limit + 1)
            after = os.fstat(source.fileno())
            if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('archive_changed_during_read')
            return data
    finally:
        os.close(descriptor)


def publish(path: Path, tables: dict[str, pa.Table], metadata: dict) -> dict:
    """Create a new private snapshot; existing directories are never overwritten."""
    blobs = {}
    for name, table in tables.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) or table.num_rows > 100_000:
            raise ValueError('archive_table_bound')
        output = io.BytesIO()
        pq.write_table(table, output, compression='zstd')
        data = output.getvalue()
        if len(data) > MAX_FILE:
            raise ValueError('archive_file_bound')
        blobs[name + '.parquet'] = (data, table.num_rows)
    if sum(len(data) for data, _ in blobs.values()) > MAX_SNAPSHOT:
        raise ValueError('archive_size_bound')
    manifest = dict(metadata, schema_version='research-snapshot-v1', files={
        name: {'sha256': digest(data), 'bytes': len(data), 'rows': rows}
        for name, (data, rows) in blobs.items()})
    manifest_data = json_bytes(manifest)
    if len(manifest_data) > MAX_FILE:
        raise ValueError('manifest_size_bound')
    path.parent.mkdir(parents=True, exist_ok=True)
    # The operator owns the parent; no untrusted party may replace write directories.
    if any(parent.is_symlink() for parent in (path.parent, *path.parent.parents)):
        raise ValueError('symlink_output')
    path.mkdir(mode=0o700)
    for name, (data, _) in blobs.items():
        with (path / name).open('xb') as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        (path / name).chmod(0o400)
    with (path / 'manifest.json').open('xb') as target:
        target.write(manifest_data)
        target.flush()
        os.fsync(target.fileno())
    (path / 'manifest.json').chmod(0o400)
    return manifest


def verified_tables(path: Path) -> tuple[dict, dict[str, pa.Table]]:
    """Return tables parsed from checksum-verified bytes, without later file reads."""
    manifest = json.loads(read_bytes(path / 'manifest.json'))
    files = manifest.get('files', {})
    if manifest.get('schema_version') != 'research-snapshot-v1' or not 1 <= len(files) <= 64:
        raise ValueError('manifest_schema')
    tables, total = {}, 0
    for name, expected in files.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}\.parquet', name):
            raise ValueError('manifest_path')
        data = read_bytes(path / name)
        total += len(data)
        if total > MAX_SNAPSHOT or len(data) != expected['bytes'] or digest(data) != expected['sha256']:
            raise ValueError('archive_checksum')
        # These small bounded files do not need the dataset reader's global I/O
        # pool, whose shutdown can hang on the supported macOS environment.
        table = pq.ParquetFile(pa.BufferReader(data)).read(use_threads=False)
        if table.num_rows != expected['rows']:
            raise ValueError('archive_rows')
        tables[name.removesuffix('.parquet')] = table
    return manifest, tables


def inspect_prices(path: Path) -> list[tuple]:
    """Run fixed DuckDB aggregation over verified Arrow data, never caller SQL."""
    import duckdb
    _, tables = verified_tables(path)
    if 'prices' not in tables:
        return []
    with duckdb.connect(':memory:', config={'enable_external_access': False}) as connection:
        connection.register('prices', tables['prices'])
        return connection.execute('SELECT ticker, count(*), min(session), max(session) '
                                  'FROM prices GROUP BY ticker ORDER BY ticker').fetchall()


def file_digest(path: Path, limit: int = 512 * 1024 * 1024) -> str:
    """Hash a bounded large evidence file without loading it into memory.

    Reject symlinks in every component and changes during the read. The larger
    limit is for combined audit evidence, not permission to fetch or publish it.
    """
    absolute = Path(os.path.abspath(path))
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor); descriptor = child
        fd = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('evidence_file_bound')
            total = 0; value = hashlib.sha256()
            while True:
                chunk = source.read(min(8*1024*1024, limit-total+1))
                if not chunk:break
                total += len(chunk)
                if total > limit:raise ValueError('evidence_file_bound')
                value.update(chunk)
            after = os.fstat(source.fileno())
            if (before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                raise ValueError('evidence_changed_during_read')
            return value.hexdigest()
    finally:
        os.close(descriptor)
