"""Task 2.5: P0 보안 — transfer_id UUID 검증 + chunk bounds + metadata schema.

감사 자료 P0 #3 (transfer_id 가 path 컴포넌트로 직접 사용) 과
P0 #4 (chunk index/size/file_path/metadata 일관성) 회귀 방지.
"""

import struct
import tempfile
import uuid
from pathlib import Path

import pytest

from core.file_transfer import (
    FileMetadata, FileTransferManager,
    CheckpointManager, Checkpoint,
    get_temp_dir, CHUNK_SIZE,
)
from core.protocol import (
    Protocol, MSG_FILE_CHUNK, is_valid_transfer_id,
)


_VALID_TID = "12345678-1234-4abc-8def-1234567890ab"
_INVALID_TIDS = [
    "../escape",
    "C:\\evil",
    "/etc/passwd",
    "..",
    "",
    "not-a-uuid",
    "12345678-1234-1234-1234-123456789012",   # version != 4
    "12345678-1234-4abc-cdef-1234567890ab",   # 4번째 그룹 first char != [89ab]
    "ABCDEF01-2345-4678-9ABC-DEF012345678",   # uppercase
    "12345678-1234-4abc-8def-1234567890ab\x00",   # null byte
    "a" * 64,
    None,
    12345,
    {"id": _VALID_TID},
]


@pytest.fixture
def manager(tmp_path):
    return FileTransferManager(download_path=str(tmp_path), max_file_size_gb=1)


# ═══════════════════════════════════════════════════════════════════════
# get_temp_dir / CheckpointManager._path_for 의 path-traversal 차단
# ═══════════════════════════════════════════════════════════════════════


def test_get_temp_dir_accepts_valid_uuid4():
    p = get_temp_dir(_VALID_TID)
    assert isinstance(p, Path)
    assert _VALID_TID in str(p)


@pytest.mark.parametrize("bad", _INVALID_TIDS)
def test_get_temp_dir_rejects_invalid(bad):
    with pytest.raises(ValueError):
        get_temp_dir(bad)


def test_checkpoint_path_for_accepts_valid_uuid4(tmp_path, monkeypatch):
    cm = CheckpointManager()
    p = cm._path_for(_VALID_TID)
    assert _VALID_TID in str(p)


@pytest.mark.parametrize("bad", _INVALID_TIDS)
def test_checkpoint_path_for_rejects_invalid(bad):
    cm = CheckpointManager()
    with pytest.raises(ValueError):
        cm._path_for(bad)


def test_checkpoint_save_load_delete_silent_on_invalid_id(caplog):
    """save/load/delete 가 invalid id 에 대해 ValueError 를 흘리지 않고 silent fail."""
    cm = CheckpointManager()
    fake = "../escape"
    cm.save(Checkpoint(transfer_id=fake))   # 예외 안 나야
    assert cm.load(fake) is None
    cm.delete(fake)   # 예외 안 나야


# ═══════════════════════════════════════════════════════════════════════
# FileMetadata.from_dict schema 검증
# ═══════════════════════════════════════════════════════════════════════


def _good_metadata_dict(transfer_id: str = _VALID_TID) -> dict:
    return {
        "transfer_id": transfer_id,
        "transfer_type": "file",
        "root_name": "x.bin",
        "files": [{"path": "x.bin", "size": 100, "hash": ""}],
        "total_size": 100,
        "file_count": 1,
    }


def test_from_dict_accepts_good_metadata():
    md = FileMetadata.from_dict(_good_metadata_dict())
    assert md.transfer_id == _VALID_TID
    assert md.total_size == 100


def test_from_dict_rejects_non_dict():
    with pytest.raises(ValueError):
        FileMetadata.from_dict("not a dict")
    with pytest.raises(ValueError):
        FileMetadata.from_dict(None)


def test_from_dict_rejects_invalid_transfer_id():
    bad = _good_metadata_dict()
    bad["transfer_id"] = "../escape"
    with pytest.raises(ValueError, match="transfer_id"):
        FileMetadata.from_dict(bad)


def test_from_dict_rejects_unknown_transfer_type():
    bad = _good_metadata_dict()
    bad["transfer_type"] = "malicious"
    with pytest.raises(ValueError, match="transfer_type"):
        FileMetadata.from_dict(bad)


def test_from_dict_rejects_total_size_mismatch():
    bad = _good_metadata_dict()
    bad["total_size"] = 999  # files.size 합계는 100
    with pytest.raises(ValueError, match="total_size"):
        FileMetadata.from_dict(bad)


def test_from_dict_rejects_file_count_mismatch():
    bad = _good_metadata_dict()
    bad["file_count"] = 5
    with pytest.raises(ValueError, match="file_count"):
        FileMetadata.from_dict(bad)


def test_from_dict_rejects_negative_file_size():
    bad = _good_metadata_dict()
    bad["files"] = [{"path": "x.bin", "size": -1, "hash": ""}]
    bad["total_size"] = -1
    with pytest.raises(ValueError, match="size"):
        FileMetadata.from_dict(bad)


def test_from_dict_rejects_unsafe_file_path():
    bad = _good_metadata_dict()
    bad["files"] = [{"path": "../etc/passwd", "size": 100, "hash": ""}]
    with pytest.raises(ValueError, match="unsafe"):
        FileMetadata.from_dict(bad)


def test_from_dict_rejects_negative_total_size():
    bad = _good_metadata_dict()
    bad["total_size"] = -100
    with pytest.raises(ValueError, match="total_size"):
        FileMetadata.from_dict(bad)


def test_from_dict_rejects_non_list_files():
    bad = _good_metadata_dict()
    bad["files"] = "not a list"
    with pytest.raises(ValueError, match="files"):
        FileMetadata.from_dict(bad)


# ═══════════════════════════════════════════════════════════════════════
# receive_chunk 의 chunk_index 경계 검증
# ═══════════════════════════════════════════════════════════════════════


def test_receive_chunk_rejects_invalid_transfer_id(manager, caplog):
    result = manager.receive_chunk(
        transfer_id="../escape",
        file_path="x.bin", chunk_index=0,
        chunk_data=b"data", expected_hash="0",
    )
    assert result is False


def test_receive_chunk_rejects_negative_chunk_index(manager, caplog):
    result = manager.receive_chunk(
        transfer_id=_VALID_TID,
        file_path="x.bin", chunk_index=-1,
        chunk_data=b"data", expected_hash="0",
    )
    assert result is False


def test_receive_chunk_rejects_non_int_chunk_index(manager, caplog):
    result = manager.receive_chunk(
        transfer_id=_VALID_TID,
        file_path="x.bin", chunk_index="0",   # 문자열
        chunk_data=b"data", expected_hash="0",
    )
    assert result is False


def test_receive_chunk_rejects_chunk_index_overflow(manager, caplog):
    """chunk_index * CHUNK_SIZE 가 max_file_size 를 넘으면 거부."""
    # max_file_size = 1 GiB = 1024 청크. 2000 번 인덱스는 overflow.
    huge_index = manager.max_file_size // CHUNK_SIZE + 1
    result = manager.receive_chunk(
        transfer_id=_VALID_TID,
        file_path="x.bin", chunk_index=huge_index,
        chunk_data=b"data", expected_hash="0",
    )
    assert result is False


# ═══════════════════════════════════════════════════════════════════════
# assemble_file / restore_folder 의 transfer_id 검증
# ═══════════════════════════════════════════════════════════════════════


def test_assemble_file_rejects_invalid_transfer_id(manager):
    result = manager.assemble_file(
        transfer_id="../escape",
        file_path="x.bin", expected_sha256="dead",
    )
    assert result is False


def test_restore_folder_rejects_invalid_transfer_id(manager):
    md = FileMetadata.from_dict(_good_metadata_dict())
    # transfer_id 를 직접 invalid 로 — restore_folder 가 인자로 받음
    result = manager.restore_folder(transfer_id="../escape", metadata=md)
    assert result == []


# ═══════════════════════════════════════════════════════════════════════
# Protocol._parse_binary_frame 조기 검증
# ═══════════════════════════════════════════════════════════════════════


def _make_chunk_meta_wire(transfer_id, file_path, chunk_index, payload=b"\x00\x01") -> bytes:
    """수동으로 binary frame meta 를 구성하여 검증 시나리오 테스트용."""
    import json
    meta = json.dumps({
        "type": MSG_FILE_CHUNK,
        "data": {
            "transfer_id": transfer_id,
            "file_path": file_path,
            "chunk_index": chunk_index,
            "hash": "00",
        },
    }, ensure_ascii=False).encode("utf-8")
    payload_body = b"\x00" + struct.pack(">I", len(meta)) + meta + payload
    return payload_body  # parse_message 는 헤더 제외 페이로드만 받음


def test_parse_binary_frame_accepts_valid_chunk():
    p = Protocol()
    wire = _make_chunk_meta_wire(_VALID_TID, "a.bin", 0)
    parsed = p.parse_message(wire)
    assert parsed is not None
    assert parsed["type"] == MSG_FILE_CHUNK
    assert parsed["data"]["transfer_id"] == _VALID_TID


def test_parse_binary_frame_rejects_invalid_transfer_id():
    p = Protocol()
    wire = _make_chunk_meta_wire("../escape", "a.bin", 0)
    assert p.parse_message(wire) is None


def test_parse_binary_frame_rejects_negative_chunk_index():
    p = Protocol()
    wire = _make_chunk_meta_wire(_VALID_TID, "a.bin", -1)
    assert p.parse_message(wire) is None


def test_parse_binary_frame_rejects_non_int_chunk_index():
    p = Protocol()
    wire = _make_chunk_meta_wire(_VALID_TID, "a.bin", "0")  # 문자열
    assert p.parse_message(wire) is None
