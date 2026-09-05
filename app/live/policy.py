from __future__ import annotations


FAIL_WORDS = ("خطا", "ناموفق", "کافی نیست", "عدم", "رد شد")
OK_WORDS = ("موفق", "ثبت شد", "انجام شد")


def notification_ok(text: str | None) -> tuple[bool, str]:
    if not text:
        return False, "NO_BROKER_NOTIFICATION"
    if any(word in text for word in FAIL_WORDS):
        return False, text
    if any(word in text for word in OK_WORDS):
        return True, text
    return False, text
