from app.logging_config import scrub_event
from app.pii import scrub_text


def test_scrub_email() -> None:
    out = scrub_text("Email me at student@vinuni.edu.vn")
    assert "student@" not in out
    assert "REDACTED_EMAIL" in out


def test_scrub_common_vietnamese_phone_formats() -> None:
    phone_numbers = (
        "0901234567",
        "090 123 4567",
        "090.123.4567",
        "090-123-4567",
        "+84 90 123 4567",
    )

    for phone_number in phone_numbers:
        out = scrub_text(f"Contact: {phone_number}")
        assert phone_number not in out
        assert "REDACTED_PHONE_VN" in out


def test_scrub_event_recurses_through_nested_payloads() -> None:
    event = {
        "payload": {
            "items": ["contact student@vinuni.edu.vn", {"phone": "090 123 4567"}],
        }
    }

    scrub_event(None, "info", event)

    assert "student@" not in event["payload"]["items"][0]
    assert "090 123 4567" not in event["payload"]["items"][1]["phone"]
