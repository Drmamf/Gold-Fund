from __future__ import annotations


FAIL_WORDS = ("خطا", "ناموفق", "کافی نیست", "عدم", "رد شد", "حداقل ارزش")
OK_WORDS = ("موفق", "ثبت شد", "انجام شد")
PENDING_WORDS = ("در حال ارسال", "ثبت سفارش در هسته")


def notification_ok(text: str | None) -> tuple[bool, str]:
    if not text:
        return False, "NO_BROKER_NOTIFICATION"
    if any(word in text for word in FAIL_WORDS):
        return False, text
    if any(word in text for word in OK_WORDS):
        return True, text
    return False, text


def order_rejected(text: str | None) -> bool:
    """True when the broker refused the ticket (min notional, insufficient power, ...)."""
    if not text:
        return False
    return any(word in text for word in FAIL_WORDS)


def order_only_queued(text: str | None) -> bool:
    """Toast says the order reached the core, not that it traded."""
    if not text:
        return False
    if order_rejected(text):
        return False
    return any(word in text for word in PENDING_WORDS)
