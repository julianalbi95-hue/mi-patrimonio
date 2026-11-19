#!/usr/bin/env python3
"""Actualiza una caché pública de precios diarios para Mi Patrimonio."""
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "prices.json")
SYMBOLS = {
    "US02079K1079": ("GOOG", "USD"),
    "LU1681048804": ("500.PA", "EUR"),
    "US67066G1040": ("NVDA", "USD"),
    "US8740541094": ("TTWO", "USD"),
    "US35834F1049": ("TE", "USD"),
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MiPatrimonioPrices/1.0)"}


def get_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def yahoo_daily_close(symbol):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol) + "?range=5d&interval=1d"
    payload = get_json(url)
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    closes = result["indicators"]["quote"][0].get("close") or []
    for timestamp, close in reversed(list(zip(timestamps, closes))):
        if close is not None and float(close) > 0:
            day = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
            return float(close), day
    raise ValueError("Yahoo no ha devuelto cierres válidos para " + symbol)


def ecb_usd_rate():
    req = urllib.request.Request(
        "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
        headers=HEADERS,
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        root = ET.fromstring(response.read())
    for cube in root.iter():
        if cube.attrib.get("currency") == "USD":
            rate_date = next((node.attrib["time"] for node in root.iter() if "time" in node.attrib), datetime.now(timezone.utc).date().isoformat())
            return float(cube.attrib["rate"]), rate_date
    raise ValueError("No se ha encontrado el cambio EUR/USD del BCE")


def main():
    try:
        with open(OUTPUT, "r", encoding="utf-8") as handle:
            previous = json.load(handle)
    except (OSError, ValueError):
        previous = {"assets": {}}

    assets = previous.get("assets", {})
    try:
        fx, fx_date = ecb_usd_rate()
        if fx > 0:
            previous["eurUsd"] = fx
            previous["fxDate"] = fx_date
    except Exception as error:
        print("Aviso: no se pudo actualizar el cambio BCE:", error)

    successes = 0
    for isin, (symbol, currency) in SYMBOLS.items():
        try:
            price, price_date = yahoo_daily_close(symbol)
            assets[isin] = {
                "ticker": symbol,
                "price": price,
                "currency": currency,
                "date": price_date,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }
            successes += 1
            print(f"{symbol}: {price} {currency} ({price_date})")
        except Exception as error:
            print(f"Aviso: {symbol} sin actualización; se conserva el último precio válido: {error}")

    previous["assets"] = assets
    previous["updatedAt"] = datetime.now(timezone.utc).isoformat()
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(previous, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    if successes == 0 and not assets:
        raise SystemExit("No se ha podido obtener ningún precio y no existe caché previa.")
    print(f"Actualizados {successes} de {len(SYMBOLS)} activos.")


if __name__ == "__main__":
    main()
