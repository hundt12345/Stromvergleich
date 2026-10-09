"""Manueller Test gegen echte Websites (benötigt Internet und `playwright install chromium`).

Aufruf:  python tests/smoke_check.py 50667 1200
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402
from app.checker import check_provider  # noqa: E402
from app.parser import best_tariff  # noqa: E402

SITES = {
    "enstroga": "https://enstroga.de/",
    "naturstrom": "https://www.naturstrom.de/stromtarife",
    "vattenfall-sales-alt": "https://www.vattenfall.de/strom/tarife/oekostrom-koeln",
}


async def main(plz: str, kwh: int):
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        for slug, url in SITES.items():
            res = await check_provider(browser, slug, url, plz, kwh)
            best = best_tariff(res["tariffs"])
            print(f"{slug}: plz_ok={res['plz_ok']} tarife={len(res['tariffs'])} note={res['note']}")
            if best:
                print(f"   bester Tarif: AP {best.ap_ct} ct, GP {best.gp_month} €/Mon, Jahr {best.annual_eur} €")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "50667", int(sys.argv[2]) if len(sys.argv) > 2 else 1200))
