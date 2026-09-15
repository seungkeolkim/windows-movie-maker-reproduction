from pathlib import Path

import pytest

from movie_maker.media import CacheKey, CacheKind, MediaCache


def test_cache_key_changes_with_source_identity_and_settings(tmp_path: Path) -> None:
    source = tmp_path / "한글 원본.mp4"
    source.write_bytes(b"first")
    first = CacheKey.create(CacheKind.PROXY, source, {"width": 960})
    other_settings = CacheKey.create(CacheKind.PROXY, source, {"width": 640})
    source.write_bytes(b"changed source")
    changed = CacheKey.create(CacheKind.PROXY, source, {"width": 960})

    assert first.digest != other_settings.digest
    assert first.digest != changed.digest
    assert not first.source_is_current()
    assert Path(first.digest).name == first.digest


def test_cache_publishes_atomically_reuses_and_cleans_partials(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    cache = MediaCache(tmp_path / "cache")
    key = CacheKey.create(CacheKind.WAVEFORM, source, {"buckets": 10})

    published = cache.publish_bytes(key, b"waveform")
    assert cache.get(key) == published
    assert published.read_bytes() == b"waveform"
    partial = cache.temporary_path(key)
    partial.write_bytes(b"partial")
    assert cache.cleanup_partials() == (partial.resolve(),)
    assert not partial.exists()


def test_cache_pruning_protects_active_and_outside_files(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path / "cache")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    first_key = CacheKey.create(CacheKind.THUMBNAIL, source, {"frame": 1})
    second_key = CacheKey.create(CacheKind.THUMBNAIL, source, {"frame": 2})
    first = cache.publish_bytes(first_key, b"a" * 10)
    second = cache.publish_bytes(second_key, b"b" * 10)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"never remove")

    with cache.lease(first):
        result = cache.prune(0)

    assert first.exists()
    assert not second.exists()
    assert second in result.removed
    assert outside.read_bytes() == b"never remove"
    assert result.bytes_remaining == 10


def test_cache_refuses_paths_outside_owned_root(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path / "cache")
    outside = tmp_path / "outside"
    outside.write_bytes(b"x")
    with pytest.raises(ValueError), cache.lease(outside):
        pass


def test_nested_cache_leases_keep_file_protected_until_last_consumer_exits(
    tmp_path: Path,
) -> None:
    cache = MediaCache(tmp_path / "cache")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    key = CacheKey.create(CacheKind.PROXY, source, {"width": 960})
    proxy = cache.publish_bytes(key, b"proxy")

    with cache.lease(proxy):
        with cache.lease(proxy):
            pass
        cache.prune(0)
        assert proxy.exists()
    cache.prune(0)
    assert not proxy.exists()
