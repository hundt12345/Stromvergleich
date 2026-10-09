"""FastAPI-Backend: streamt den Vergleich live als Server-Sent Events.

Start:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from playwright.async_api import async_playwright

from .checker import check_provider
from .parser import best_tariff
from .providers import Provider, load_providers

STATIC = Path(__file__).resolve().parent / "static"
CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "cache.json"
CACHE_TTL = 12 * 3600          # Sekunden: Tarifdaten pro Anbieter/PLZ für 12 h wiederverwenden
MAX_PARALLEL = 3               # Browser-Tabs gleichzeitig (Rücksicht auf Anbieter und Hardware)

state: dict = {}


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["providers"] = load_providers()
    state["pw"] = await async_playwright().start()
    state["browser"] = await state["pw"].chromium.launch(args=["--no-sandbox"])
    state["sem"] = asyncio.Semaphore(MAX_PARALLEL)
    state["cache"] = _load_cache()
    yield
    await state["browser"].close()
    await state["pw"].stop()


app = FastAPI(title="Stromvergleich", lifespan=lifespan)


def _annual(kwh: int, tariff: dict) -> float | None:
    if tariff.get("gp_month") is None:
        return None
    return round(kwh * tariff["ap_ct"] / 100 + 12 * tariff["gp_month"], 2)


async def _evaluate(p: Provider, plz: str, kwh: int) -> dict:
    row = {**p.public(), "info_found": False, "plz_ok": None, "gp_month": None,
           "ap_ct": None, "annual_eur": None, "note": "", "snippet": "", "from_cache": False}
    if not p.website:
        row["note"] = "Keine Website bekannt"
        return row

    cache = state["cache"]
    # Preise können vom Verbrauch abhängen (Staffeln), deshalb gehört kWh in den Schlüssel
    key = f"{p.slug}|{plz}|{kwh}"
    entry = cache.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        result = entry["result"]
        row["from_cache"] = True
    else:
        async with state["sem"]:
            result = await check_provider(state["browser"], p.slug, p.website, plz, kwh)
        # Nur Ergebnisse cachen, die nicht durch Fehler entstanden sind
        if not result["note"].startswith(("Zeitüberschreitung", "Abruf fehlgeschlagen")):
            cache[key] = {"ts": time.time(), "result": {
                "tariffs": [t.as_dict() for t in result["tariffs"]],
                "plz_ok": result["plz_ok"], "note": result["note"]}}
            _save_cache(cache)
        result = {"tariffs": [t.as_dict() for t in result["tariffs"]],
                  "plz_ok": result["plz_ok"], "note": result["note"]}

    tariffs = result["tariffs"]
    row["note"] = result["note"]
    row["plz_ok"] = result["plz_ok"]
    row["info_found"] = bool(tariffs)
    # Kosten neu berechnen, damit der Verbrauch aus der Anfrage gilt (Cache ist verbrauchsunabhängig)
    for t in tariffs:
        t["annual_eur"] = _annual(kwh, t)
    best = best_tariff([_obj(t) for t in tariffs])
    if best:
        row.update({"gp_month": best.gp_month, "ap_ct": best.ap_ct,
                    "annual_eur": best.annual_eur, "snippet": best.snippet})
    return row


class _obj:  # kleines Adapter-Objekt für best_tariff (erwartet Attribute)
    def __init__(self, d: dict):
        self.__dict__.update(d)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/providers")
async def providers():
    return {"count": len(state["providers"]),
            "with_website": sum(1 for p in state["providers"] if p.website),
            "providers": [p.public() for p in state["providers"]]}


@app.get("/api/compare")
async def compare(plz: str = Query(..., pattern=r"^\d{5}$"),
                  kwh: int = Query(..., ge=100, le=50000),
                  limit: int = Query(0, ge=0, description="Nur die ersten N Anbieter (0 = alle); für Tests")):
    if not re.fullmatch(r"\d{5}", plz):
        raise HTTPException(400, "PLZ muss 5-stellig sein")

    async def stream():
        providers_ = state["providers"][:limit] if limit else state["providers"]
        yield _sse({"type": "start", "total": len(providers_), "plz": plz, "kwh": kwh})
        tasks = [asyncio.create_task(_evaluate(p, plz, kwh)) for p in providers_]
        done_count = 0
        rows: list[dict] = []
        for fut in asyncio.as_completed(tasks):
            row = await fut
            done_count += 1
            rows.append(row)
            yield _sse({"type": "provider", "row": row, "done": done_count})
        top = sorted([r for r in rows if r["annual_eur"] is not None and r["plz_ok"] is not False],
                     key=lambda r: r["annual_eur"])[:5]
        stats = {
            "total": len(rows),
            "with_website": sum(1 for r in rows if r["website"]),
            "info_found": sum(1 for r in rows if r["info_found"]),
            "plz_ok": sum(1 for r in rows if r["plz_ok"] is True),
            "plz_not_ok": sum(1 for r in rows if r["plz_ok"] is False),
            "plz_unknown": sum(1 for r in rows if r["plz_ok"] is None),
        }
        yield _sse({"type": "done", "top5": top, "stats": stats})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
