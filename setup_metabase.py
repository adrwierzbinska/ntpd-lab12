"""Automatyczna konfiguracja Metabase przez REST API.

Tworzy konto admina, polaczenie z baza `sensors`, kolekcje "IoT monitoring",
trzy pytania + trend + wskazniki KPI oraz dashboard z filtrem strefy. Dziala na
swiezej instancji Metabase (krok /api/setup). Stan zapisuje do mb_state.json.
"""

import json
import time
import uuid

import requests

BASE = "http://localhost:3000"
ADMIN = {
    "first_name": "Ada",
    "last_name": "Nowak",
    "email": "ada@sensors.local",
    "password": "Sensors!2026Ada",
}

session = requests.Session()


def api(method, path, **kw):
    r = session.request(method, BASE + path, **kw)
    if not r.ok:
        print("ERR", method, path, r.status_code, r.text[:400])
        r.raise_for_status()
    return r.json() if r.text else {}


def main():
    # 1. konto admina + sesja (swieza instancja)
    token = api("GET", "/api/session/properties")["setup-token"]
    res = api("POST", "/api/setup", json={
        "token": token,
        "user": ADMIN,
        "prefs": {"site_name": "Sensor Monitoring BI", "allow_tracking": False},
    })
    session.headers.update({"X-Metabase-Session": res["id"]})
    print("admin + sesja OK")

    # 2. polaczenie z hurtownia PostgreSQL (baza sensors)
    db_id = api("POST", "/api/database", json={
        "name": "Sensor DWH (PostgreSQL)",
        "engine": "postgres",
        "details": {"host": "postgres", "port": 5432, "dbname": "sensors",
                    "user": "bi", "password": "bi", "ssl": False},
    })["id"]
    print("baza dodana, id =", db_id)

    # 3. sync + odczekanie na metadane tabeli readings
    api("POST", f"/api/database/{db_id}/sync_schema")
    table_id, fields = None, {}
    for _ in range(40):
        meta = api("GET", f"/api/database/{db_id}/metadata")
        for t in meta.get("tables", []):
            if t["name"] == "readings":
                table_id = t["id"]
                fields = {f["name"]: f["id"] for f in t.get("fields", [])}
        if table_id and {"state", "zone", "value", "reading_time"} <= set(fields):
            break
        time.sleep(2)
    assert table_id, "brak tabeli readings po sync"
    f_state, f_zone, f_val = fields["state"], fields["zone"], fields["value"]
    print("tabela readings id =", table_id)

    coll_id = api("POST", "/api/collection",
                  json={"name": "IoT monitoring", "color": "#509EE3"})["id"]

    def mbql(query):
        return {"type": "query", "database": db_id, "query": query}

    def native(sql):
        tag = {"zone_filter": {"id": str(uuid.uuid4()), "name": "zone_filter",
               "display-name": "Zone", "type": "dimension",
               "dimension": ["field", f_zone, None], "widget-type": "string/=", "default": None}}
        return {"type": "native", "database": db_id, "native": {"query": sql, "template-tags": tag}}

    def card(name, dq, display):
        cid = api("POST", "/api/card", json={"name": name, "dataset_query": dq,
                  "display": display, "visualization_settings": {}, "collection_id": coll_id})["id"]
        print(f"  pytanie '{name}' id={cid} ({display})")
        return cid

    cards = {}
    # 3.1 kreator wizualny (bez SQL): liczba odczytow wg statusu -> kolowy
    cards["pie_state"] = card("3.1 Odczyty wg statusu",
        mbql({"source-table": table_id, "aggregation": [["count"]],
              "breakout": [["field", f_state, None]]}), "pie")
    # 3.2 kreator wizualny: liczba + srednia wartosc wg strefy -> slupkowy
    cards["bar_zone"] = card("3.2 Liczba i srednia wartosc wg strefy",
        mbql({"source-table": table_id,
              "aggregation": [["count"], ["avg", ["field", f_val, None]]],
              "breakout": [["field", f_zone, None]]}), "bar")
    # 3.3 zapisane jako SQL: alerty wg strefy -> tabela (z field-filter strefy)
    cards["sql_alerts"] = card("3.3 Alerty wg strefy (SQL)", native(
        "SELECT zone,\n"
        "       COUNT(*) FILTER (WHERE state='alert') AS alerts,\n"
        "       COUNT(*) AS total,\n"
        "       ROUND(100.0*COUNT(*) FILTER (WHERE state='alert')/COUNT(*),1) AS alert_pct\n"
        "FROM readings\nWHERE 1=1 [[AND {{zone_filter}}]]\n"
        "GROUP BY zone ORDER BY alerts DESC"), "table")
    # trend (ocena 5): srednia wartosc po dniu -> liniowy
    cards["trend"] = card("Trend sredniej wartosci w czasie (po dniu)", native(
        "SELECT date_trunc('day', reading_time)::date AS day,\n"
        "       ROUND(AVG(value)::numeric,1) AS avg_value\n"
        "FROM readings\nWHERE 1=1 [[AND {{zone_filter}}]]\nGROUP BY 1 ORDER BY 1"), "line")
    # KPI (scalary)
    cards["kpi_total"] = card("KPI: Liczba odczytow",
        native("SELECT COUNT(*) FROM readings [[WHERE {{zone_filter}}]]"), "scalar")
    cards["kpi_avg"] = card("KPI: Srednia wartosc",
        native("SELECT ROUND(AVG(value)::numeric,1) FROM readings [[WHERE {{zone_filter}}]]"), "scalar")
    cards["kpi_alert"] = card("KPI: Odsetek alertow (%)", native(
        "SELECT ROUND(100.0*COUNT(*) FILTER (WHERE state='alert')/COUNT(*),1) "
        "FROM readings [[WHERE {{zone_filter}}]]"), "scalar")
    # podglad tabeli (do zad. 2)
    cards["preview"] = card("Podglad tabeli readings",
        mbql({"source-table": table_id, "limit": 20}), "table")

    # 4. dashboard z filtrem strefy (KPI na gorze, szczegoly nizej)
    dash_id = api("POST", "/api/dashboard",
                  json={"name": "Monitoring czujnikow - przeglad", "collection_id": coll_id})["id"]
    param_id = "zone_param"
    layout = [
        ("kpi_total", 0, 0, 6, 4), ("kpi_avg", 6, 0, 6, 4), ("kpi_alert", 12, 0, 6, 4),
        ("bar_zone", 0, 4, 12, 6), ("pie_state", 12, 4, 6, 6),
        ("trend", 0, 10, 12, 6), ("sql_alerts", 12, 10, 6, 6),
    ]
    native_cards = {"sql_alerts", "trend", "kpi_total", "kpi_avg", "kpi_alert"}
    dashcards = []
    for i, (key, x, y, w, h) in enumerate(layout):
        if key in native_cards:
            target = ["dimension", ["template-tag", "zone_filter"]]
        else:
            target = ["dimension", ["field", f_zone, None]]
        dashcards.append({"id": -(i + 1), "card_id": cards[key], "row": y, "col": x,
                          "size_x": w, "size_y": h, "visualization_settings": {},
                          "parameter_mappings": [{"parameter_id": param_id,
                                                  "card_id": cards[key], "target": target}]})
    api("PUT", f"/api/dashboard/{dash_id}", json={
        "dashcards": dashcards,
        "parameters": [{"id": param_id, "name": "Strefa", "slug": "zone",
                        "type": "string/=", "sectionId": "string"}],
    })
    print("dashboard id =", dash_id, "z filtrem 'Strefa' i", len(dashcards), "kartami")

    with open("mb_state.json", "w") as f:
        json.dump({"db_id": db_id, "table_id": table_id, "collection_id": coll_id,
                   "cards": cards, "dashboard_id": dash_id, "admin": ADMIN}, f, indent=2)
    print("ZAPISANO mb_state.json")


if __name__ == "__main__":
    main()
