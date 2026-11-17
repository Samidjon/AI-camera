from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from aci_agent.camera.models import CameraConfig, CameraSecret, SourceType
from aci_agent.security.crypto import SecretBox, SecretDecryptionError, load_or_create_key
from aci_agent.storage.database import Database
from aci_agent.storage.repository import CameraRepository, DuplicateNameError


def _repo(tmp_path: Path, key: bytes | None = None) -> tuple[CameraRepository, Database]:
    db = Database(tmp_path / "agent.db")
    return CameraRepository(db, SecretBox(key or Fernet.generate_key())), db


def test_secret_is_encrypted_at_rest(tmp_path: Path):
    repo, db = _repo(tmp_path)
    cam = repo.create(
        CameraConfig(id="", name="Gate", source_type=SourceType.RTSP, source_uri="rtsp://10.0.0.5/live"),
        CameraSecret("admin", "Sup3r-S3cret!"),
    )
    raw = db.query("SELECT ciphertext FROM camera_secrets WHERE camera_id = ?", (cam.id,))[0][0]
    assert b"Sup3r-S3cret!" not in raw and b"admin" not in raw
    assert b"Sup3r-S3cret!" not in (tmp_path / "agent.db").read_bytes()
    assert repo.get_secret(cam.id).password == "Sup3r-S3cret!"
    # cameras table holds no credentials at all
    row = dict(db.query("SELECT * FROM cameras WHERE id = ?", (cam.id,))[0])
    assert all("Sup3r" not in str(v) for v in row.values())
    db.close()


def test_wrong_key_cannot_decrypt(tmp_path: Path):
    repo, db = _repo(tmp_path)
    cam = repo.create(
        CameraConfig(id="", name="Gate", source_type=SourceType.RTSP, source_uri="rtsp://h/x"), CameraSecret("a", "b")
    )
    db.close()
    other, db2 = _repo(tmp_path, Fernet.generate_key())
    with pytest.raises(SecretDecryptionError):
        other.get_secret(cam.id)
    db2.close()


def test_key_file_created_once_and_reused(tmp_path: Path):
    path = tmp_path / "keys" / "secret.key"
    k1 = load_or_create_key(path)
    k2 = load_or_create_key(path)
    assert k1 == k2 and path.exists()


def test_delete_camera_cascades_secret(tmp_path: Path):
    repo, db = _repo(tmp_path)
    cam = repo.create(
        CameraConfig(id="", name="G", source_type=SourceType.RTSP, source_uri="rtsp://h/x"), CameraSecret("a", "b")
    )
    assert repo.delete(cam.id)
    assert db.query("SELECT COUNT(*) FROM camera_secrets")[0][0] == 0
    db.close()


def test_groups_unique_and_set_null_on_delete(tmp_path: Path):
    repo, db = _repo(tmp_path)
    g = repo.create_group("Entrances", None)
    with pytest.raises(DuplicateNameError):
        repo.create_group("entrances", None)  # case-insensitive
    cam = repo.create(CameraConfig(id="", name="G", source_type=SourceType.USB, source_uri="0", group_id=g["id"]), None)
    assert repo.list_groups()[0]["camera_count"] == 1
    repo.delete_group(g["id"])
    assert repo.get(cam.id).group_id is None
    db.close()


def test_schema_migration_is_idempotent(tmp_path: Path):
    db = Database(tmp_path / "agent.db")
    v = db.schema_version
    db.close()
    db = Database(tmp_path / "agent.db")
    assert db.schema_version == v >= 1
    assert db.integrity_ok()
    db.close()


def test_deleted_data_is_overwritten_on_disk(tmp_path: Path):
    """secure_delete: a deleted camera's name must not survive in the database file's free pages."""
    repo, db = _repo(tmp_path)
    assert db.query("PRAGMA secure_delete")[0][0] == 1
    marker = "PRIVATE-CAMERA-NAME-7f3a"
    cam = repo.create(CameraConfig(id="", name=marker, source_type=SourceType.FILE, source_uri="x.mp4"), None)
    repo.delete(cam.id)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("agent.db*"))
    assert marker.encode() not in raw
