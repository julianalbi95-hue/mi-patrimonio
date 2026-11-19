#!/usr/bin/env python3
"""Actualiza precios diarios usando una lista pública de ISIN y conserva el último precio válido."""
import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "prices.json")
SUPABASE_URL = "https://ulheujreaunsahulnxeb.supabase.co"
SUPABASE_KEY = "sb_publishable_H1O0sr2bstyoFTcM6XAcQA_EYW5lE31"
STATIC = {
    "US02079K1079": ("GOOG", "USD"),
    "LU1681048804": ("500.PA", "EUR"),
    "US67066G1040": ("NVDA", "USD"),
    "US8740541094": ("TTWO", "USD"),
    "US35834F1049": ("TE", "USD"),
}
# OpenFIGI exchange code -> Yahoo Finance suffix. Only recognized listings are used.
EXCHANGE_SUFFIX = {
    "US": "", "UN": "", "UW": "", "UA": "",
    "FP": ".PA", "GY": ".DE", "GR": ".DE", "GF": ".F",
    "LN": ".L", "NA": ".AS", "SW": ".SW", "IM": ".MI",
    "BB": ".BR", "SM": ".MC", "ID": ".IR", "SS": ".ST",
    "DC": ".CO", "FH": ".HE", "NO": ".OL", "PL": ".LS",
    "AV": ".VI", "AU": ".AX", "CT": ".TO", "CN": ".V",
    "JT": ".T", "HK": ".HK", "SP": ".SI",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MiPatrimonioPrices/1.0)"}


def request(url, data=None, headers=None):
    merged = dict(HEADERS)
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=data, headers=merged)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def get_json(url):
    return json.loads(request(url).decode("utf-8"))


def public_isins():
    url = SUPABASE_URL + "/rest/v1/public_investment_isins?select=isin"
    raw = request(url, headers={"apikey": SUPABASE_KEY, "Authorization": "Bearer " + SUPABASE_KEY})
    rows = json.loads(raw.decode("utf-8"))
    return sorted({str(row.get("isin", "")).strip().upper() for row in rows if row.get("isin")})


def map_isins(isins):
    unknown = [isin for isin in isins if isin not in STATIC]
    mapped = {}
    # OpenFIGI accepts up to 10 mapping jobs per unauthenticated request.
    for start in range(0, len(unknown), 10):
        batch = unknown[start:start + 10]
        jobs = [{"idType": "ID_ISIN", "idValue": isin} for isin in batch]
        try:
            payload = json.loads(request(
                "https://api.openfigi.com/v3/mapping",
                data=json.dumps(jobs).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            ).decode("utf-8"))
        except Exception as error:
            print("Aviso: OpenFIGI no disponible:", error)
            continue
        for isin, result in zip(batch, payload):
            hits = result.get("data") or []
            # Evita seleccionar warrants/opciones/bonos o símbolos sin sufijo conocido.
            candidates = [hit for hit in hits if hit.get("ticker") and hit.get("exchCode") in EXCHANGE_SUFFIX]
            candidates = [hit for hit in candidates if any(term in str(hit.get("securityType", "")).lower()
                          for term in ("common stock", "depositary receipt", "etp", "exchange traded fund", "fund"))]
            if not candidates:
                print("Aviso: no se ha encontrado una cotización compatible para", isin)
                continue
            # Prioriza la cotización estadounidense cuando exista; si no, la primera bolsa reconocida.
            candidates.sort(key=lambda hit: (hit.get("exchCode") != "US", hit.get("exchCode", "")))
            hit = candidates[0]
            symbol = str(hit["ticker"]) + EXCHANGE_SUFFIX[hit["exchCode"]]
            mapped[isin] = (symbol, None)
            print("ISIN resuelto:", isin, "->", symbol, "(" + str(hit.get("name", "")) + ")")
        time.sleep(1)
    return mapped


def yahoo_quote(symbol):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol) + "?range=5d&interval=1d"
    payload = get_json(url)
    result = payload["chart"]["result"][0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    closes = result["indicators"]["quote"][0].get("close") or []
    for timestamp, close in reversed(list(zip(timestamps, closes))):
        if close is not None and float(close) > 0:
            day = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
            currency = str(meta.get("currency") or "").upper()
            if currency not in ("EUR", "USD"):
                raise ValueError("Divisa no compatible: " + currency)
            return float(close), currency, day
    raise ValueError("Yahoo no ha devuelto cierres válidos para " + symbol)


def ecb_usd_rate():
    root = ET.fromstring(request("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"))
    for cube in root.iter():
        if cube.attrib.get("currency") == "USD":
            rate_date = next((node.attrib["time"] for node in root.iter() if "time" in node.attrib),
                             datetime.now(timezone.utc).date().isoformat())
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
            previous["eurUsd"], previous["fxDate"] = fx, fx_date
    except Exception as error:
        print("Aviso: no se pudo actualizar el cambio BCE:", error)

    try:
        isins = public_isins()
        print("ISIN públicos recibidos:", len(isins))
    except Exception as error:
        print("Aviso: no se pudo leer la lista pública de ISIN; se actualizan los cinco activos iniciales:", error)
        isins = list(STATIC)

    symbols = dict(STATIC)
    symbols.update(map_isins(isins))
    successes = 0
    for isin in isins:
        if isin not in symbols:
            continue
        symbol, fixed_currency = symbols[isin]
        try:
            price, detected_currency, price_date = yahoo_quote(symbol)
            currency = fixed_currency or detected_currency
            assets[isin] = {
                "ticker": symbol, "price": price, "currency": currency,
                "date": price_date, "updatedAt": datetime.now(timezone.utc).isoformat(),
            }
            successes += 1
            print(f"{isin} / {symbol}: {price} {currency} ({price_date})")
        except Exception as error:
            print(f"Aviso: {isin} / {symbol} sin actualización; se conserva el último precio válido: {error}")

    previous["assets"] = assets
    previous["updatedAt"] = datetime.now(timezone.utc).isoformat()
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(previous, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if successes == 0 and not assets:
        raise SystemExit("No se ha podido obtener ningún precio y no existe caché previa.")
    print(f"Actualizados {successes} precios; {len(assets)} activos conservados en caché.")


if __name__ == "__main__":
    main()
