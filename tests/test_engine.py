import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402

pytestmark = pytest.mark.skipif(not engine.available(), reason="native engine not built (python build_engine.py)")


def _payload(tmp_path, size=100_003):
    p = tmp_path / "in.bin"
    p.write_bytes(os.urandom(size // 2) + b"A" * (size - size // 2))
    return p


def test_roundtrip(tmp_path):
    src = _payload(tmp_path)
    shards = tmp_path / "shards"
    engine.shatter(str(src), "k1", out_dir=str(shards))
    assert len(list(shards.glob("fragment_*.ansx"))) == 12
    out = tmp_path / "out.bin"
    engine.unshatter(str(shards), str(out), "k1")
    assert out.read_bytes() == src.read_bytes()


def test_empty_and_tiny_files(tmp_path):
    for data in (b"", b"x", b"hello world"):
        src = tmp_path / "t.bin"
        src.write_bytes(data)
        d = tmp_path / f"s{len(data)}"
        engine.shatter(str(src), "k", out_dir=str(d))
        out = tmp_path / "o.bin"
        engine.unshatter(str(d), str(out), "k")
        assert out.read_bytes() == data


def test_any_8_of_12_shards_suffice(tmp_path):
    src = _payload(tmp_path, 5000)
    shards = tmp_path / "shards"
    engine.shatter(str(src), "k", out_dir=str(shards))
    import itertools, random
    files = sorted(shards.glob("fragment_*.ansx"))
    combos = list(itertools.combinations(range(12), 4))
    random.Random(1).shuffle(combos)
    for drop in combos[:25]:           # delete 4 shards, keep 8
        work = tmp_path / f"w{'_'.join(map(str, drop))}"
        work.mkdir()
        for i, f in enumerate(files):
            if i not in drop:
                (work / f.name).write_bytes(f.read_bytes())
        out = tmp_path / "o.bin"
        engine.unshatter(str(work), str(out), "k")
        assert out.read_bytes() == src.read_bytes()


def test_seven_shards_fail(tmp_path):
    src = _payload(tmp_path, 2000)
    shards = tmp_path / "shards"
    engine.shatter(str(src), "k", out_dir=str(shards))
    for f in sorted(shards.glob("fragment_*.ansx"))[:5]:
        f.unlink()
    with pytest.raises(engine.EngineError) as e:
        engine.unshatter(str(shards), str(tmp_path / "o"), "k")
    assert e.value.code == -10


def test_wrong_key_and_wrong_second_factor(tmp_path):
    src = _payload(tmp_path, 2000)
    shards = tmp_path / "shards"
    engine.shatter(str(src), "right", second_factor="pin", out_dir=str(shards))
    for key, sf in (("wrong", "pin"), ("right", ""), ("right", "other")):
        with pytest.raises(engine.EngineError) as e:
            engine.unshatter(str(shards), str(tmp_path / "o"), key, sf)
        assert e.value.code == -11
    assert not (tmp_path / "o").exists()          # no output on auth failure
    engine.unshatter(str(shards), str(tmp_path / "ok"), "right", "pin")


def test_corrupt_shard_is_skipped_via_crc(tmp_path):
    src = _payload(tmp_path, 4000)
    shards = tmp_path / "shards"
    engine.shatter(str(src), "k", out_dir=str(shards))
    f = shards / "fragment_1.ansx"
    b = bytearray(f.read_bytes())
    b[-1] ^= 0xFF
    f.write_bytes(bytes(b))
    out = tmp_path / "o"
    engine.unshatter(str(shards), str(out), "k")   # 11 valid shards remain
    assert out.read_bytes() == src.read_bytes()


def test_tamper_with_header_is_detected(tmp_path):
    src = _payload(tmp_path, 4000)
    shards = tmp_path / "shards"
    engine.shatter(str(src), "k", out_dir=str(shards))
    # Flip a data byte in 5 shards and fix nothing: CRC drops them, leaving 7 -> not enough.
    for f in sorted(shards.glob("fragment_*.ansx"))[:5]:
        b = bytearray(f.read_bytes())
        b[60] ^= 1
        f.write_bytes(bytes(b))
    with pytest.raises(engine.EngineError):
        engine.unshatter(str(shards), str(tmp_path / "o"), "k")


def test_codec_differs_per_vault_and_shards_hide_plaintext(tmp_path):
    src = tmp_path / "in.bin"
    src.write_bytes(b"SECRET-MARKER-" * 5000)
    a, b = tmp_path / "a", tmp_path / "b"
    engine.shatter(str(src), "k", out_dir=str(a))
    engine.shatter(str(src), "k", out_dir=str(b))
    assert (a / "fragment_1.ansx").read_bytes() != (b / "fragment_1.ansx").read_bytes()  # random salt+nonce
    for f in a.glob("fragment_*.ansx"):
        assert b"SECRET-MARKER" not in f.read_bytes()


def test_new_vault_removes_stale_shards(tmp_path):
    src = _payload(tmp_path, 1000)
    d = tmp_path / "s"
    engine.shatter(str(src), "k1", out_dir=str(d))
    (d / "fragment_12.ansx").write_bytes(b"junk")
    engine.shatter(str(src), "k2", out_dir=str(d))
    assert len(list(d.glob("fragment_*.ansx"))) == 12
    engine.unshatter(str(d), str(tmp_path / "o"), "k2")
