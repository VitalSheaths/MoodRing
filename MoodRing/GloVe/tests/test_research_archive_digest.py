"""Large evidence hashing stays bounded and rejects symlink substitution."""
import hashlib
import pytest
from research_data.archive import file_digest


def test_hash_and_size_bound(tmp_path):
    p=tmp_path/'evidence';p.write_bytes(b'fictional evidence')
    assert file_digest(p)==hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='bound'):file_digest(p,limit=3)


def test_file_and_parent_symlinks_rejected(tmp_path):
    p=tmp_path/'data';p.mkdir();(p/'a').write_bytes(b'a')
    (tmp_path/'link').symlink_to(p,target_is_directory=True)
    (p/'link').symlink_to(p/'a')
    with pytest.raises(OSError):file_digest(tmp_path/'link/a')
    with pytest.raises(OSError):file_digest(p/'link')
