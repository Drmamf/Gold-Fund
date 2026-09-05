from __future__ import annotations

import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    import easyocr
except ImportError:
    easyocr = None


_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_NUM_RE = re.compile(r"[\d۰-۹٠-٩]+(?:[٬,]\d{3})*")
_OCR_READER = None
logger = logging.getLogger("wallex_gold.live.karamad")


def normalize_number(text: str) -> str:
    s = (text or "").translate(_DIGIT_MAP)
    for sep in ("٬", "،", ",", " ", "\u00a0"):
        s = s.replace(sep, "")
    return s.strip()


def parse_int(text: str) -> int | None:
    n = normalize_number(text)
    return int(n) if n.isdigit() else None


def wait_visible(driver, locator, timeout=20):
    return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(locator))


def wait_clickable(driver, locator, timeout=20):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))


def clear_field(element):
    driver = element.parent
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center'}); arguments[0].focus();",
        element,
    )
    driver.execute_script(
        """
        const el = arguments[0];
        el.value = '';
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        """,
        element,
    )
    try:
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.BACKSPACE)
    except Exception:
        pass


def human_type(element, text, min_delay=0.04, max_delay=0.10):
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(min_delay, max_delay))


def set_native_value(driver, element, value: str):
    driver.execute_script(
        """
        const el = arguments[0];
        const val = String(arguments[1]);
        el.scrollIntoView({block:'center'});
        el.focus();
        const desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
        desc.set.call(el, '');
        el.dispatchEvent(new Event('input', {bubbles:true}));
        desc.set.call(el, val);
        el.dispatchEvent(new InputEvent('input', {bubbles:true, data: val, inputType:'insertText'}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
        """,
        element,
        value,
    )


def _get_ocr_reader():
    global _OCR_READER
    if easyocr is None:
        return None
    if _OCR_READER is None:
        _OCR_READER = easyocr.Reader(["en"], gpu=False)
    return _OCR_READER


def _linux_chrome_options(*, user_data_dir: Path, headless: bool = False) -> Options:
    options = Options()
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if headless:
        options.add_argument("--headless=new")
    chrome_bin = os.environ.get("CHROME_BIN") or os.environ.get("GOOGLE_CHROME_BIN")
    if not chrome_bin:
        for candidate in (
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ):
            if os.path.exists(candidate):
                chrome_bin = candidate
                break
    if chrome_bin:
        options.binary_location = chrome_bin
    return options


def _chromedriver_path() -> str | None:
    """Prefer a local chromedriver. Iranian VPS often cannot fetch Chrome for Testing (403)."""
    env = os.environ.get("CHROMEDRIVER") or os.environ.get("CHROMEDRIVER_PATH")
    candidates = [env] if env else []
    candidates.extend(
        [
            "/usr/local/bin/chromedriver",
            "/usr/bin/chromedriver",
            "/opt/wallex-gold/Gold-Fund/bin/chromedriver",
        ]
    )
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def setup_driver(*, user_data_dir: Path, headless: bool = False):
    options = _linux_chrome_options(user_data_dir=user_data_dir, headless=headless)
    driver_path = _chromedriver_path()
    if driver_path:
        return webdriver.Chrome(service=Service(driver_path), options=options)
    return webdriver.Chrome(options=options)


class KaramadClient:
    """Linux port of the Karamad UI driver. One long-lived Chrome session."""

    def __init__(
        self,
        *,
        username: str,
        password: str,
        login_url: str,
        dashboard_url: str,
        artifact_dir: Path,
        user_data_dir: Path,
        confirm_seconds: float = 2.0,
        headless: bool = False,
    ):
        self.username = username
        self.password = password
        self.login_url = login_url
        self.dashboard_url = dashboard_url
        self.artifact_dir = artifact_dir
        self.user_data_dir = user_data_dir
        self.confirm_seconds = confirm_seconds
        self.headless = headless
        self.driver = None
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        if self.driver is not None:
            return
        self.driver = setup_driver(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
        )

    def close(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                logger.exception("Chrome quit failed")
            self.driver = None

    def is_logged_in(self) -> bool:
        if self.driver is None:
            return False
        try:
            url = (self.driver.current_url or "").lower()
        except WebDriverException:
            return False
        return "dashboard" in url or "premium" in url

    def ensure_dashboard(self) -> None:
        self.start()
        if self.is_logged_in():
            if "premium/stock" not in (self.driver.current_url or "").lower():
                self.driver.get(self.dashboard_url)
                wait_visible(self.driver, (By.CSS_SELECTOR, "app-premium-stock"), timeout=30)
            return
        if not self.login():
            raise RuntimeError("KARAMAD_LOGIN_FAILED")
        self.dismiss_modal()
        if "premium/stock" not in (self.driver.current_url or "").lower():
            self.driver.get(self.dashboard_url)
            wait_visible(self.driver, (By.CSS_SELECTOR, "app-premium-stock"), timeout=30)

    def login(self) -> bool:
        self.start()
        driver = self.driver
        driver.get(self.login_url)
        time.sleep(random.uniform(2.0, 3.0))
        username_field = wait_visible(driver, (By.CSS_SELECTOR, "app-login form input"))
        clear_field(username_field)
        human_type(username_field, self.username)
        password_field = wait_visible(driver, (By.CSS_SELECTOR, "app-login lib-virtual-keyboard input"))
        clear_field(password_field)
        human_type(password_field, self.password)
        captcha_text = self.solve_captcha()
        captcha_input = wait_visible(driver, (By.CSS_SELECTOR, "app-login app-captcha input"))
        clear_field(captcha_input)
        human_type(captcha_input, captcha_text)
        wait_clickable(driver, (By.CSS_SELECTOR, "app-login form button")).click()
        try:
            WebDriverWait(driver, 25).until(
                lambda d: "dashboard" in (d.current_url or "").lower()
                or "premium" in (d.current_url or "").lower()
            )
            return True
        except TimeoutException:
            logger.error("Karamad login timeout url=%s", driver.current_url)
            self.read_notification(timeout=5)
            self.save_debug("login_failed")
            return False

    def solve_captcha(self) -> str:
        driver = self.driver
        captcha_img = wait_visible(driver, (By.CSS_SELECTOR, "app-login app-captcha img"), timeout=20)
        raw_path = self.artifact_dir / "captcha.png"
        processed_path = self.artifact_dir / "captcha_processed.png"
        captcha_img.screenshot(str(raw_path))
        img = Image.open(raw_path)
        img = ImageEnhance.Contrast(img).enhance(3.2)
        img = img.filter(ImageFilter.SHARPEN)
        img = img.convert("L")
        img = ImageOps.autocontrast(img, cutoff=2)
        img = img.point(lambda x: 0 if x < 125 else 255)
        img.save(processed_path)

        text = ""
        if pytesseract is not None:
            text = pytesseract.image_to_string(
                img,
                config="--psm 7 -c tessedit_char_whitelist=0123456789",
            )
            text = re.sub(r"\D", "", text or "")
        if len(text) < 4:
            reader = _get_ocr_reader()
            if reader is not None:
                result = reader.readtext(
                    str(processed_path), detail=0, allowlist="0123456789", paragraph=False
                )
                text = re.sub(r"\D", "", "".join(result).strip())
        if not text:
            raise RuntimeError("CAPTCHA_OCR_FAILED")
        logger.info("Captcha digits=%s", text)
        return text

    def dismiss_modal(self):
        try:
            close_btn = wait_clickable(
                self.driver,
                (
                    By.XPATH,
                    "//div[contains(@class,'bg-danger') and contains(@class,'cursor-pointer')]"
                    "//app-svg-icon[@icon='img-ico-close']",
                ),
                timeout=5,
            )
            close_btn.click()
            time.sleep(1)
        except TimeoutException:
            pass

    def read_balances(self) -> dict[str, int]:
        el = wait_visible(self.driver, (By.CSS_SELECTOR, "app-account-balance-summary"), timeout=20)
        raw = el.text
        labels = [
            "قدرت خرید سهام",
            "قدرت خرید مشتقه",
            "مجموع بلوکه",
            "مانده نهایی",
            "پرتفوی لحظه",
        ]
        found: dict[str, int] = {}
        for label in labels:
            m = re.search(re.escape(label) + r"[^\d۰-۹\n]*\n?\s*([\d٬,]+)", raw)
            if m:
                value = parse_int(m.group(1))
                if value is not None:
                    found[label] = value
        return found

    def order_side_label(self, side: str) -> str:
        word = "خرید" if side == "buy" else "فروش"
        labels = []
        for b in self.driver.find_elements(
            By.XPATH, f"//app-dashboard-common-order-panel//button[contains(., '{word}')]"
        ):
            if b.is_displayed():
                labels.append((b.text or "").replace("\n", " ").strip())
        return " | ".join(labels)

    def select_symbol(self, symbol: str):
        driver = self.driver
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass
        time.sleep(0.4)
        box = wait_clickable(driver, (By.CSS_SELECTOR, "lib-search-inline-dashboard"), timeout=20)
        box.click()
        time.sleep(0.4)
        search_input = wait_visible(driver, (By.CSS_SELECTOR, "lib-search-inline-dashboard input"), timeout=10)
        clear_field(search_input)
        human_type(search_input, symbol)
        WebDriverWait(driver, 12).until(
            lambda d: d.find_elements(
                By.CSS_SELECTOR,
                "ngb-typeahead-window button, [id^='ngb-typeahead-'] button, ngb-typeahead-window [role='option']",
            )
            or d.find_elements(By.CSS_SELECTOR, "[id^='ngb-typeahead-']")
        )
        options = []
        for sel in (
            "ngb-typeahead-window button",
            "ngb-typeahead-window [role='option']",
            "[id^='ngb-typeahead-'] button",
            "[id^='ngb-typeahead-']",
        ):
            options = [o for o in driver.find_elements(By.CSS_SELECTOR, sel) if o.is_displayed()]
            if options:
                break
        texts = [(o.text or "").strip() for o in options]
        chosen = None
        chosen_text = ""
        for o in options:
            txt = (o.text or "").strip()
            if "\n" in txt:
                continue
            first = txt.split()[0] if txt else ""
            if first == symbol or txt.startswith(symbol + " ") or txt.startswith(symbol + "("):
                chosen = o
                chosen_text = txt
                break
        if chosen is None:
            raise RuntimeError("no exact option for %s: %s" % (symbol, texts))
        driver.execute_script("arguments[0].click();", chosen)

        def panel_ready(d):
            label = self.order_side_label("buy")
            return bool(label) and symbol in label

        try:
            WebDriverWait(driver, 20).until(panel_ready)
        except TimeoutException:
            raise RuntimeError(
                "order panel still: %s not %s" % (self.order_side_label("buy"), symbol)
            )
        logger.info("order panel ok symbol=%s clicked=%s", symbol, chosen_text)

    def read_threshold_prices(self) -> tuple[int, int]:
        el = wait_visible(self.driver, (By.CSS_SELECTOR, "lib-range-indicator"), timeout=15)
        raw = el.text.strip() or (el.get_attribute("textContent") or "")
        nums = [parse_int(m.group(0)) for m in _NUM_RE.finditer(raw)]
        nums = [n for n in nums if n is not None]
        if len(nums) < 2:
            raise ValueError(f"آستانه خوانا نیست: {raw!r}")
        low, high = min(nums[0], nums[1]), max(nums[0], nums[1])
        return high, low

    def clamp_price(self, price: int) -> int:
        high, low = self.read_threshold_prices()
        if price < low:
            return low
        if price > high:
            return high
        return price

    def fill_order_panel(self, price: int, quantity: int):
        driver = self.driver
        wait_visible(driver, (By.CSS_SELECTOR, "app-dashboard-common-order-panel"), timeout=15)
        inputs = [
            i for i in driver.find_elements(By.CSS_SELECTOR, "app-dashboard-common-order-panel input")
            if i.is_displayed()
        ]
        price_el = next(
            (
                i
                for i in inputs
                if (i.get_attribute("id") or "").lower() == "price"
                or (i.get_attribute("name") or "").lower() == "price"
            ),
            None,
        )
        qty_el = next(
            (
                i
                for i in inputs
                if i != price_el
                and (
                    (i.get_attribute("id") or "").lower() in {"quantity", "qty", "volume", "count"}
                    or (i.get_attribute("name") or "").lower() in {"quantity", "qty", "volume", "count"}
                )
            ),
            None,
        )
        if qty_el is None:
            qty_el = next((i for i in inputs if i != price_el), None)
        if price_el is None or qty_el is None or price_el == qty_el:
            raise RuntimeError(f"فیلد قیمت/تعداد جدا نشد n={len(inputs)}")

        price_s, qty_s = str(price), str(quantity)
        set_native_value(driver, price_el, price_s)
        time.sleep(0.3)
        set_native_value(driver, qty_el, qty_s)
        time.sleep(0.3)
        actual_price = normalize_number(price_el.get_attribute("value") or "")
        actual_qty = normalize_number(qty_el.get_attribute("value") or "")
        if actual_price != price_s or actual_qty != qty_s:
            raise RuntimeError(
                f"فرم جور نیست. انتظار {price_s}/{qty_s} | فرم {actual_price}/{actual_qty}"
            )

    def click_side(self, side: str, symbol: str, *, actually_click: bool) -> str:
        word = "خرید" if side == "buy" else "فروش"
        btn = wait_clickable(
            self.driver,
            (By.XPATH, f"//app-dashboard-common-order-panel//button[contains(., '{word}')]"),
            timeout=10,
        )
        label = (btn.text or "").replace("\n", " ").strip()
        if symbol not in label:
            raise RuntimeError(f"button {label} is not {symbol}")
        time.sleep(self.confirm_seconds)
        if not actually_click:
            logger.info("DRY_RUN skip click %s", label)
            return f"DRY_RUN:{label}"
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();",
            btn,
        )
        logger.info("clicked %s", label)
        return label

    def read_sellable_qty(self) -> Optional[int]:
        panel = wait_visible(self.driver, (By.CSS_SELECTOR, "app-dashboard-common-order-panel"), timeout=10)
        raw = panel.text or ""
        for label in ("قابل فروش", "موجودی", "مانده"):
            m = re.search(re.escape(label) + r"[^\d۰-۹]{0,12}([\d٬,]+)", raw)
            if m:
                value = parse_int(m.group(1))
                if value is not None and value > 0:
                    return value
        return None

    def read_notification(self, timeout=8) -> Optional[str]:
        try:
            notif = wait_visible(self.driver, (By.CSS_SELECTOR, "app-notification"), timeout=timeout)
            text = (notif.text or "").strip()
            return text or None
        except TimeoutException:
            return None

    def save_debug(self, prefix: str = "error") -> None:
        try:
            png = self.artifact_dir / f"{prefix}.png"
            html = self.artifact_dir / f"{prefix}.html"
            self.driver.save_screenshot(str(png))
            html.write_text(self.driver.page_source or "", encoding="utf-8")
        except Exception:
            logger.exception("debug artifact failed")

    def place_limit(
        self,
        *,
        symbol: str,
        side: str,
        price: int,
        quantity: int,
        actually_click: bool,
    ) -> tuple[str, Optional[str]]:
        self.ensure_dashboard()
        self.select_symbol(symbol)
        limited = self.clamp_price(int(price))
        self.fill_order_panel(limited, int(quantity))
        label = self.click_side(side, symbol, actually_click=actually_click)
        notif = None
        if actually_click:
            time.sleep(3)
            notif = self.read_notification()
        return label, notif
