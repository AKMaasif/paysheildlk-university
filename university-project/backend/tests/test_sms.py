import pytest

from app.notify import BLOCKED_SMS, GENERIC_SMS, UnsafeSms, assert_safe_sms


def test_generic_sms_has_no_link_or_code():
    assert_safe_sms(GENERIC_SMS)
    assert_safe_sms(BLOCKED_SMS)
    assert "http" not in GENERIC_SMS.lower()
    assert "http" not in BLOCKED_SMS.lower()


def test_sms_rejects_links_and_codes():
    with pytest.raises(UnsafeSms):
        assert_safe_sms("Open https://pay.example to confirm")
    with pytest.raises(UnsafeSms):
        assert_safe_sms("Your PayShield code is 449122")
