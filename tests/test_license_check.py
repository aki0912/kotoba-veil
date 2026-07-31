from scripts.check_licenses import is_approved, is_approved_exception


def test_license_allowlist_accepts_permissive_and_rejects_copyleft() -> None:
    assert is_approved("MIT")
    assert is_approved("Apache Software License")
    assert is_approved("BSD License")
    assert not is_approved("GPL-3.0-only")
    assert not is_approved("CC-BY-SA-4.0")
    assert not is_approved("UNKNOWN")


def test_only_named_packages_receive_mpl_exception() -> None:
    assert is_approved_exception("certifi", "MPL-2.0")
    assert is_approved_exception("tqdm", "MPL-2.0 AND MIT")
    assert not is_approved_exception("another-package", "MPL-2.0")
    assert not is_approved_exception("certifi", "GPL-3.0-only")
