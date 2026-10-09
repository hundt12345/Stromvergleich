"""Prüft eine Anbieter-Website per Headless-Browser: PLZ eingeben, Verbrauch eingeben,
Ergebnis auslesen.

Jeder Anbieter hat eigene Formulare. Diese Datei enthält eine generische Strategie,
die auf vielen Tarifrechnern funktioniert. Anbieter mit abweichendem Ablauf lassen sich
über ADAPTERS ergänzen (Funktion mit Signatur wie `generic_check`).
"""
from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable

from playwright.async_api import Browser, Page

from .parser import Tariff, detect_unavailable, parse_tariffs

COOKIE_LABELS = [
    "Alle akzeptieren", "Alle Cookies akzeptieren", "Alles akzeptieren", "Akzeptieren",
    "Alle annehmen", "Zustimmen", "Geht klar", "Einverstanden",
]
SUBMIT_LABELS = [
    "Tarife vergleichen", "Jetzt berechnen", "Jetzt vergleichen", "Tarife anzeigen",
    "Tarife berechnen", "Berechnen", "Vergleichen", "Weiter", "Tarife ansehen", "Zu den Tarifen",
]
PLZ_ATTR = re.compile(r"postal|postcode|plz|zip|postleit", re.IGNORECASE)
KWH_ATTR = re.compile(r"consum|verbrauch|kwh", re.IGNORECASE)


async def _accept_cookies(page: Page) -> None:
    for label in COOKIE_LABELS:
        try:
            await page.get_by_role("button", name=re.compile(label, re.IGNORECASE)).first.click(timeout=1000)
            await page.wait_for_timeout(400)
            return
        except Exception:
            continue


async def _fill_form(page: Page, plz: str, kwh: int) -> dict:
    found = {"plz": False, "kwh": False}
    inputs = page.locator("input:visible")
    for i in range(await inputs.count()):
        el = inputs.nth(i)
        try:
            attrs = " ".join(filter(None, [
                await el.get_attribute("name"), await el.get_attribute("placeholder"),
                await el.get_attribute("id"), await el.get_attribute("aria-label"),
                await el.get_attribute("autocomplete")]))
            readonly = await el.get_attribute("readonly")
        except Exception:
            continue
        if readonly is not None:
            continue
        if not found["plz"] and PLZ_ATTR.search(attrs):
            try:
                await el.fill(plz, timeout=1500)
                found["plz"] = True
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(500)
            except Exception:
                pass
        elif not found["kwh"] and KWH_ATTR.search(attrs):
            try:
                await el.fill(str(kwh), timeout=1500)
                found["kwh"] = True
            except Exception:
                pass
    return found


async def _submit(page: Page) -> None:
    for label in SUBMIT_LABELS:
        try:
            await page.get_by_role("button", name=re.compile(label, re.IGNORECASE)).first.click(timeout=800)
            await page.wait_for_timeout(3000)
            return
        except Exception:
            continue
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(3000)


async def generic_check(browser: Browser, url: str, plz: str, kwh: int) -> dict:
    """Gibt ein Ergebnis-Dict zurück. Keys: tariffs, plz_ok, form, note."""
    ctx = await browser.new_context(locale="de-DE", viewport={"width": 1366, "height": 900})
    page = await ctx.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1500)
        await _accept_cookies(page)
        form = await _fill_form(page, plz, kwh)
        if form["plz"] or form["kwh"]:
            await _submit(page)
        text = await page.inner_text("body")
        tariffs = parse_tariffs(text, kwh)
        if tariffs:
            plz_ok: bool | None = True
            note = "Preise im Seitentext gefunden (generische Auswertung)"
        elif detect_unavailable(text):
            plz_ok = False
            note = "Seite meldet: keine Belieferung für diese PLZ"
        else:
            plz_ok = None
            note = "Keine Tarifdaten erkannt" if form["plz"] or form["kwh"] else "Kein PLZ-Formular erkannt"
        return {"tariffs": tariffs, "plz_ok": plz_ok, "form": form, "note": note}
    finally:
        await ctx.close()


async def _read_page_text(browser: Browser, url: str, wait_ms: int = 2500) -> str:
    ctx = await browser.new_context(locale="de-DE", viewport={"width": 1366, "height": 900})
    page = await ctx.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1500)
        await _accept_cookies(page)
        await page.wait_for_timeout(wait_ms)
        return await page.inner_text("body")
    finally:
        await ctx.close()


async def vattenfall_check(browser: Browser, url: str, plz: str, kwh: int) -> dict:
    """Vattenfall: das Verbrauchsfeld ist schreibgeschützt. PLZ und Verbrauch werden deshalb
    als URL-Parameter übergeben; die Ergebnisse stehen dann direkt im Seitentext."""
    target = (f"https://www.vattenfall.de/strom/tarife/oekostrom-koeln"
              f"?postalCode={plz}&consumption={kwh}&city=K%C3%B6ln&energyTypeSelected=electricity")
    text = await _read_page_text(browser, target, wait_ms=6000)
    tariffs = parse_tariffs(text, kwh)
    if tariffs:
        plz_ok: bool | None = True
    elif detect_unavailable(text):
        plz_ok = False
    else:
        plz_ok = None
    note = ("Vattenfall-Adapter (URL-Parameter), Preise ohne Bonus"
            if tariffs else "Vattenfall-Adapter: keine Tarifdaten erkannt")
    return {"tariffs": tariffs, "plz_ok": plz_ok, "form": {"url_params": True}, "note": note}


Adapter = Callable[[Browser, str, str, int], Awaitable[dict]]
# Spezialisierte Adapter je Anbieter-Slug. Fallback ist generic_check.
ADAPTERS: dict[str, Adapter] = {
    "vattenfall-sales-alt": vattenfall_check,
}


async def check_provider(browser: Browser, slug: str, url: str, plz: str, kwh: int, timeout: float = 60) -> dict:
    adapter = ADAPTERS.get(slug, generic_check)
    try:
        return await asyncio.wait_for(adapter(browser, url, plz, kwh), timeout=timeout)
    except asyncio.TimeoutError:
        return {"tariffs": [], "plz_ok": None, "form": {}, "note": "Zeitüberschreitung beim Abruf"}
    except Exception as exc:  # Website nicht erreichbar, Seitenfehler etc.
        return {"tariffs": [], "plz_ok": None, "form": {}, "note": f"Abruf fehlgeschlagen: {type(exc).__name__}"}
