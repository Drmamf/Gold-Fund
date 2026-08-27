from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional

from app.contracts import FundSnapshot, StrategySignal


SEP = "━━━━━━━━━━━━━━━━━━━━"


def money(value: Any) -> str:
    try:
        return f"{Decimal(str(value)):,.0f} ریال"
    except Exception:
        return "—"


def pct_fraction(value: Any, digits: int = 2) -> str:
    try:
        return f"{Decimal(str(value)) * Decimal('100'):+.{digits}f}٪"
    except Exception:
        return "—"


def pct_magnitude(value: Any, digits: int = 2) -> str:
    try:
        return (
            f"{abs(Decimal(str(value))) * Decimal('100'):.{digits}f}٪"
        )
    except Exception:
        return "—"


def pp_fraction(value: Any, digits: int = 2) -> str:
    try:
        return f"{Decimal(str(value)) * Decimal('100'):+.{digits}f}pp"
    except Exception:
        return "—"


def num(value: Any, digits: int = 0) -> str:
    try:
        return f"{Decimal(str(value)):,.{digits}f}"
    except Exception:
        return "—"


def safe_text(value: Any, limit: int = 500) -> str:
    text = str(value or "—")
    for ch in ("*", "_", "[", "]", "`"):
        text = text.replace(ch, "")
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return text


def strategy_title(strategy_id: str) -> str:
    if strategy_id == "RELATIVE_BUY_HOLD":
        return "Strategy A — عیار پایه + سوییچ نسبی"
    if strategy_id == "THRESHOLD_10_10_RELATIVE":
        return "Strategy B — صندوق‌های طلا | آستانه‌ای + سوییچ نسبی"
    return strategy_id


def open_account_card(strategy_id: str, report: Mapping[str, Any]) -> str:
    title = strategy_title(strategy_id)
    icon = "🔵" if strategy_id == "RELATIVE_BUY_HOLD" else "🟡"

    if not report.get("exists"):
        return (
            f"🔔  ** شروع بازار | 12:00 ** \n"
            f"{icon}  ** {title} ** \n"
            f"{SEP}\n"
            f"📭 سابقه حساب قبلی موجود نیست.\n"
            f"حساب در اولین اجرای معتبر مقداردهی خواهد شد.\n"
            f"{SEP}\n"
            f"🕒 وضعیت: آماده پایش بازار"
        )

    base = [
        "🔔  ** شروع بازار | 12:00 ** ",
        f"{icon}  ** {title} ** ",
        SEP,
        f"🗓 آخرین ثبت: {safe_text(report.get('captured_at'))}",
        f"💼 ارزش پرتفوی: ** {money(report.get('portfolio_value'))} ** ",
        f"📈 بازده کل: ** {pct_fraction(report.get('total_return'))} ** ",
        f"📉 حداکثر افت سرمایه از ابتدا: ** {pct_magnitude(report.get('max_drawdown'))} ** ",
        f"💰 سود/زیان تحقق‌یافته: {money(report.get('realized_pnl'))}",
        f"🌊 سود/زیان شناور: {money(report.get('unrealized_pnl'))}",
        SEP,
    ]

    if strategy_id == "RELATIVE_BUY_HOLD":
        base.extend([
            f"🏷 صندوق فعلی: ** {safe_text(report.get('current_fund'))} ** ",
            f"🔢 تعداد واحد: {num(report.get('units'))}",
            f"💵 وجه نقد: {money(report.get('cash'))}",
            f"🔁 تعداد سوییچ‌ها: {report.get('rotations_count', 0)}",
        ])
    else:
        funds = "، ".join(report.get("active_funds") or []) or "—"
        base.extend([
            f"🥇 ارزش طلا: {money(report.get('gold_exposure'))}",
            f"📊 سهم طلا: {pct_fraction(report.get('gold_exposure_ratio'))}",
            f"🏦 ارزش آفران: {money(report.get('fixed_income_value'))}",
            f"💵 وجه نقد: {money(report.get('cash'))}",
            f"📌 پوزیشن‌های باز: {report.get('active_positions_count', 0)}",
            f"🏷 صندوق‌های فعال: {funds}",
            f"🔁 مجموع سوییچ پوزیشن‌های باز: {report.get('rotations_count', 0)}",
        ])

    base.extend([SEP, "🟢 وضعیت: ** آماده پایش بازار ** "])
    return "\n".join(base)


def operational_start_card(at: datetime) -> str:
    return "\n".join([
        "🟢  ** شروع تایم کاری و محاسبات | 12:05 ** ",
        SEP,
        "⚙️ موتور مشترک بازار فعال شد.",
        "🧮 Valuation و Relative Value در حال پایش هستند.",
        "🔵 Strategy A: عیار پایه + سوییچ نسبی",
        "🟡 Strategy B: صندوق‌های طلا",
        "",
        "📡 از این لحظه تا 17:59 فقط ** سیگنال‌های جدید ** گزارش می‌شوند.",
        "📊 در ساعت 18:00 گزارش پایان تایم معاملات ارسال خواهد شد.",
        SEP,
        f"🕒 {at.strftime('%Y-%m-%d %H:%M:%S')}",
    ])


def api_error_card(
    *,
    source: str,
    operation: str,
    error: str,
    occurred_at: datetime,
    instrument_symbol: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> str:
    lines = [
        "⚠️  ** هشدار دریافت داده API ** ",
        SEP,
        f"🛰 منبع: ** {safe_text(source)} ** ",
        f"⚙️ عملیات: {safe_text(operation)}",
    ]
    if instrument_symbol:
        lines.append(f"🏷 ابزار/صندوق: {safe_text(instrument_symbol)}")
    if endpoint:
        lines.append(f"🔗 Endpoint: {safe_text(endpoint, 220)}")
    lines.extend([
        f"❌ خطا: {safe_text(error, 700)}",
        SEP,
        "🛑 داده این منبع در این Cycle قابل اتکا نیست.",
        f"🕒 {occurred_at.strftime('%Y-%m-%d %H:%M:%S')}",
    ])
    return "\n".join(lines)


def _symbol(fund_id: Optional[int], funds: Mapping[int, FundSnapshot]) -> str:
    if fund_id is None:
        return "—"
    snap = funds.get(int(fund_id))
    return snap.symbol if snap else str(fund_id)


def signal_card(
    signal: StrategySignal,
    funds: Mapping[int, FundSnapshot],
    *,
    at: datetime,
) -> str:
    source = _symbol(signal.source_fund_id, funds)
    target = _symbol(signal.target_fund_id or signal.fund_id, funds)
    p = signal.payload or {}

    if signal.strategy_id == "RELATIVE_BUY_HOLD":
        return "\n".join([
            "🔵  ** سیگنال Strategy A | سوییچ نسبی ** ",
            SEP,
            f"🔄 اقدام پیشنهادی: ** {source} → {target} ** ",
            "",
            f"📐 Relative Score مبدأ: {pp_fraction(signal.relative_score)}",
            f"🎯 مزیت خام: {pp_fraction(signal.gross_edge)}",
            f"↔️ هزینه Bid/Ask: {pp_fraction(signal.spread_cost)}",
            f"💸 هزینه کارمزد: {pp_fraction(signal.fee_cost)}",
            f"✅ مزیت خالص قابل اجرا: ** {pp_fraction(signal.net_executable_edge)} ** ",
            f"🧱 حداقل لازم: +{p.get('min_required_pct_points', '0.50')}pp",
            SEP,
            "📌 اقدام استراتژی: ** فروش کامل مبدأ و انتقال کامل موقعیت به مقصد ** ",
            "ℹ️ این پیام فقط Signal است؛ نتیجه Execution جداگانه ثبت می‌شود.",
            f"🕒 {at.strftime('%H:%M:%S')}",
        ])

    # Strategy B
    if signal.signal_type == "THRESHOLD_SELL":
        return "\n".join([
            "🔴  ** سیگنال Strategy B | خروج آستانه‌ای ** ",
            SEP,
            f"📍 Position: #{p.get('position_id', '—')}",
            f"🏷 صندوق فعلی: ** {source} ** ",
            f"🫧 Total Bubble: ** {pct_fraction(signal.total_bubble)} ** ",
            f"🚪 Sell Threshold: {pct_fraction(p.get('sell_threshold'))}",
            SEP,
            "📌 اقدام استراتژی: ** خروج کامل همین Position ** ",
            "📏 مبنا: Sell Threshold صندوق فعلی پس از هر سوییچ",
            "ℹ️ این پیام فقط Signal است.",
            f"🕒 {at.strftime('%H:%M:%S')}",
        ])

    if signal.signal_type == "ROTATE_TO":
        return "\n".join([
            "🟣  ** سیگنال Strategy B | سوییچ موقعیت ** ",
            SEP,
            f"📍 Position: #{p.get('position_id', '—')}",
            f"🔄 اقدام پیشنهادی: ** {source} → {target} ** ",
            f"🧬 نوع ورود اولیه: {safe_text(p.get('origin_entry_type'))}",
            f"🎯 مزیت خام: {pp_fraction(signal.gross_edge)}",
            f"↔️ هزینه Bid/Ask: {pp_fraction(signal.spread_cost)}",
            f"💸 هزینه کارمزد: {pp_fraction(signal.fee_cost)}",
            f"✅ مزیت خالص: ** {pp_fraction(signal.net_executable_edge)} ** ",
            f"🧱 حداقل لازم: +{p.get('min_required_edge_pct_points', '0.50')}pp",
            SEP,
            "📌 Exposure طلا تغییر نمی‌کند؛ فقط محل نگهداری این Tranche تغییر می‌کند.",
            "ℹ️ این پیام فقط Signal است.",
            f"🕒 {at.strftime('%H:%M:%S')}",
        ])

    if signal.signal_type == "MA7_FALLBACK_BUY_2":
        return "\n".join([
            "🟠  ** سیگنال Strategy B | Entry 2 — MA7 Averaging Down ** ",
            SEP,
            "🧭 مسیر: ** MA7 Fallback ** ",
            "📉 بازیابی +1.50pp هنوز اتفاق نیفتاده است.",
            f"📊 ارزش معاملات تجمیعی امروز: {money(p.get('current_total_trade_value'))}",
            f"📚 میانگین 7 روز کامل: {money(p.get('previous_7d_average_trade_value'))}",
            "✅ شرط ارزش معاملات: پاس شده",
            "",
            f"🏷 صندوق منتخب: ** {target} ** ",
            f"🫧 Total Bubble: ** {pct_fraction(signal.total_bubble)} ** ",
            f"💼 اندازه ورود: {p.get('allocation_pct', '10')}٪ پرتفوی",
            SEP,
            "📌 اقدام استراتژی: ** ورود پله دوم برای کاهش میانگین ** ",
            "ℹ️ این پیام فقط Signal است.",
            f"🕒 {at.strftime('%H:%M:%S')}",
        ])

    # Threshold entry N.
    # Only the best threshold candidate is shown in Bale.
    # All candidates remain persisted in the DB.
    stage_display = str(
        signal.signal_stage or "Threshold Entry"
    ).replace("_", " ")

    route_raw = str(
        p.get("entry_route", "THRESHOLD_REARM")
    )
    route_display = route_raw.replace("_", " ").title()

    candidate_count = int(
        p.get("threshold_candidate_count") or 1
    )

    lines = [
        f"🟡  ** سیگنال Strategy B | {stage_display} ** ",
        SEP,
    ]

    if candidate_count > 1:
        lines.append(
            f"🎯 کاندیداهای عبورکرده از Threshold: "
            f"{candidate_count} صندوق"
        )
        lines.append(
            f"🏆 صندوق منتخب: ** {target} ** "
        )
    else:
        lines.append(
            f"🏷 صندوق منتخب: ** {target} ** "
        )

    lines.extend([
        f"🧭 مسیر: {route_display}",
        f"🫧 Total Bubble: ** {pct_fraction(signal.total_bubble)} ** ",
        f"🎚 Buy Threshold: {pct_fraction(p.get('buy_threshold'))}",
        f"🔓 Rearm Level: {pct_fraction(p.get('rearm_threshold'))}",
        f"💼 اندازه ورود: {p.get('allocation_pct', '10')}٪ پرتفوی",
        SEP,
        f"📌 اقدام استراتژی: ** ورود پله "
        f"{p.get('entry_number', '—')} به {target} ** ",
        "ℹ️ فقط Signal منتخب در بله گزارش می‌شود؛ "
        "تمام کاندیداها در دیتابیس ثبت می‌شوند.",
        f"🕒 {at.strftime('%H:%M:%S')}",
    ])

    return "\n".join(lines)


def close_account_card(
    strategy_id: str,
    report: Mapping[str, Any],
) -> str:
    title = strategy_title(strategy_id)
    icon = "🔵" if strategy_id == "RELATIVE_BUY_HOLD" else "🟡"

    lines = [
        "🧾  ** پایان تایم معاملات | 18:00 ** ",
        f"{icon}  ** {title} ** ",
        SEP,
        f"💼 ارزش پرتفوی: ** {money(report.get('portfolio_value'))} ** ",
        f"📈 بازده کل: ** {pct_fraction(report.get('total_return'))} ** ",
        f"📉 حداکثر افت سرمایه از ابتدا: ** {pct_magnitude(report.get('max_drawdown'))} ** ",
        f"☀️ تغییر ارزش امروز: ** {money(report.get('daily_pnl'))} ** ",
        f"💰 P&L تحقق‌یافته: {money(report.get('realized_pnl'))}",
        f"🌊 P&L شناور: {money(report.get('unrealized_pnl'))}",
        f"💸 کارمزد تجمعی: {money(report.get('fees_total'))}",
        SEP,
    ]

    if strategy_id == "RELATIVE_BUY_HOLD":
        lines.extend([
            f"🏷 صندوق فعلی: ** {safe_text(report.get('current_fund'))} ** ",
            f"🔢 تعداد واحد: {num(report.get('units'))}",
            f"🔁 کل سوییچ‌ها: {report.get('rotations_count', 0)}",
            f"📨 سیگنال‌های امروز: {report.get('signals_today', 0)}",
        ])
    else:
        funds = "، ".join(report.get("active_funds") or []) or "—"
        lines.extend([
            f"🥇 ارزش طلا: {money(report.get('gold_exposure'))}",
            f"📊 سهم طلا: {pct_fraction(report.get('gold_exposure_ratio'))}",
            f"🏦 ارزش آفران: {money(report.get('fixed_income_value'))}",
            f"📌 پوزیشن باز: {report.get('active_positions_count', 0)}",
            f"🏷 صندوق‌های فعال: {funds}",
            f"🟡 ورودهای امروز: {report.get('entries_today', 0)}",
            f"🔴 خروج‌های امروز: {report.get('exits_today', 0)}",
            f"🟣 سوییچ‌های امروز: {report.get('rotations_today', 0)}",
            f"📨 کل سیگنال امروز: {report.get('signals_today', 0)}",
        ])

    lines.extend([
        SEP,
        "✅ وضعیت حساب برای روز معاملاتی بعد ثبت شد.",
    ])
    return "\n".join(lines)


def signals_file_caption(
    *,
    date_text: str,
    count_a: int,
    count_b: int,
) -> str:
    return "\n".join([
        "📎  ** فایل سیگنال‌های روز ** ",
        SEP,
        f"🗓 تاریخ: {date_text}",
        f"🔵 Strategy A: {count_a} سیگنال",
        f"🟡 Strategy B: {count_b} سیگنال",
        f"📨 مجموع: ** {count_a + count_b} ** ",
        "",
        "فایل شامل همه Signalهاست؛ چه اجرا شده باشند چه نشده باشند.",
    ])


def backup_caption(
    *,
    date_text: str,
    table_count: int,
    file_size_bytes: int,
) -> str:
    return "\n".join([
        "🗄  ** نسخه هفتگی دیتابیس | چهارشنبه 18:00 ** ",
        SEP,
        f"🗓 تاریخ: {date_text}",
        f"🧱 تعداد جداول: {table_count}",
        f"📦 حجم فایل: {file_size_bytes / (1024*1024):.2f} MB",
        "✅ شامل Market Data، Valuation، Relative، Signals، Transactions، Positions، Accounts و Logs.",
        SEP,
        "📎 بکاپ کامل CSVها در فایل ZIP پیوست شده است.",
    ])


def asset_composition_reminder_card(item: Mapping[str, Any]) -> str:
    return "\n".join([
        "📅  ** یادآوری ترکیب دارایی صندوق ** ",
        SEP,
        f"🏷 صندوق: ** {safe_text(item.get('symbol'))} ** ",
        f"🗓 دوره مورد انتظار: ** {safe_text(item.get('expected_period_end_jalali'))} ** ",
        f"📂 آخرین ترکیب ثبت‌شده: {safe_text(item.get('latest_composition_as_of_jalali'))}",
        "",
        "⚠️ ترکیب دارایی دوره جدید هنوز در دیتابیس ثبت نشده است.",
        "📌 پس از دریافت گزارش جدید، CSV ترکیب دارایی را به‌روزرسانی و seed کنید.",
        SEP,
        "🔁 این یادآوری در روزهای کاری تکرار می‌شود تا composition جدید ثبت شود.",
    ])