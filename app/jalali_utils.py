from __future__ import annotations

from datetime import date


_G_D_M = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def gregorian_to_jalali(g: date) -> tuple[int, int, int]:
    gy, gm, gd = g.year, g.month, g.day
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621

    gy2 = gy + 1 if gm > 2 else gy
    days = (
        365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        - 80
        + gd
        + _G_D_M[gm - 1]
    )

    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365

    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date:
    if jy > 979:
        gy = 1600
        jy -= 979
    else:
        gy = 621

    days = (
        365 * jy
        + (jy // 33) * 8
        + ((jy % 33) + 3) // 4
        + 78
        + jd
        + ((jm - 1) * 31 if jm < 7 else 186 + (jm - 7) * 30)
    )

    gy += 400 * (days // 146097)
    days %= 146097

    if days > 36524:
        gy += 100 * ((days - 1) // 36524)
        days = (days - 1) % 36524
        if days >= 365:
            days += 1

    gy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365

    gd = days + 1
    month_lengths = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if gy % 4 == 0 and (gy % 100 != 0 or gy % 400 == 0):
        month_lengths[2] = 29

    gm = 1
    while gm <= 12 and gd > month_lengths[gm]:
        gd -= month_lengths[gm]
        gm += 1
    return date(gy, gm, gd)


def jalali_month_length(year: int, month: int) -> int:
    if 1 <= month <= 6:
        return 31
    if 7 <= month <= 11:
        return 30
    if month != 12:
        raise ValueError("Jalali month must be 1..12")
    # Esfand length can be inferred by Gregorian distance to next Farvardin 1.
    next_year = jalali_to_gregorian(year + 1, 1, 1)
    esfand_1 = jalali_to_gregorian(year, 12, 1)
    return (next_year - esfand_1).days


def jalali_date_text(year: int, month: int, day: int) -> str:
    return f"{year:04d}/{month:02d}/{day:02d}"


def parse_jalali_date(text: str) -> date:
    y, m, d = map(int, text.replace("-", "/").split("/"))
    return jalali_to_gregorian(y, m, d)
