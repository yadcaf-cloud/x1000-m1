#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 M1 — collecte PMU en lecture seule sur les données M1 protégées.

python pmu_engine.py --self-test
python pmu_engine.py --date 2026-10-31
python pmu_engine.py --dry-run

Sorties exclusives : data/collecte_m1_pmu.json et data/diagnostic_collecte_m1.json.
Aucun import automatique dans m1.py, aucune écriture sur courses.json ou FIGÉs.
"""
import argparse
import datetime as dt
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PARIS = ZoneInfo("Europe/Paris")
API = os.environ.get("PMU_API_BASE", "https://online.turfinfo.api.pmu.fr/rest/client/7").rstrip("/")
OUTPUTS = {"collecte_m1_pmu.json", "diagnostic_collecte_m1.json"}
FOREIGN = ("BELGIQUE", "SUISSE", "ITALIE", "ALLEMAGNE", "ESPAGNE", "ANGLETERRE", "ROYAUME-UNI", "IRLANDE", "ETATS-UNIS", "ÉTATS-UNIS", "SUEDE", "SUÈDE", "NORVEGE", "NORVÈGE", "DANEMARK", "AFRIQUE DU SUD", "HONG KONG", "JAPON", "AUSTRALIE", "PAYS-BAS", "HOLLANDE", "PORTUGAL")


def now():
    return dt.datetime.now(PARIS)


def get_json(path):
    url = API + "/" + path.lstrip("/")
    request = urllib.request.Request(url, headers={"User-Agent": "X1000-M1-PMU-Collector/1.0", "Accept": "application/json"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.status != 200:
                    raise ValueError("HTTP " + str(response.status))
                data = response.read(8_000_001)
                if len(data) > 8_000_000:
                    raise ValueError("Réponse trop volumineuse")
                return json.loads(data.decode("utf-8-sig"))
        except (urllib.error.URLError, TimeoutError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                break
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("Échec PMU " + path + " : " + str(last))


def first(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        value = obj.get(key)
        if value is not None and value != "":
            return value
    return None


def integer(value):
    try:
        if isinstance(value, bool) or value is None:
            return None
        result = int(value)
        return result if result > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def text(value):
    if isinstance(value, str):
        return value.strip() or None
    return None


def paris_depart(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            if not 0 < value < 10**16:
                return None
            stamp = value / 1000 if value > 10**11 else value
            return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).astimezone(PARIS).isoformat()
        if isinstance(value, str):
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            return parsed.astimezone(PARIS).isoformat()
    except (ValueError, OverflowError, OSError, TypeError):
        pass
    return None


def location_france(reunion):
    """True uniquement avec code pays explicite FR/FRA/FRANCE."""
    country = first(reunion, "codePays", "pays", "countryCode")
    if isinstance(country, dict):
        country = first(country, "code", "nom", "libelle")
    if isinstance(country, str):
        value = country.strip().upper()
        if value in ("FR", "FRA", "FRANCE"):
            return True
        if value:
            return False
    # Ne pas deviner la France depuis le seul nom d'hippodrome.
    return None


def normalize_runner(raw):
    number = integer(first(raw, "numPmu", "numero", "num", "numeroPmu"))
    name = text(first(raw, "nom", "nomCheval", "name"))
    if number is None or name is None:
        return None
    np = first(raw, "nonPartant", "non_partant") is True or str(first(raw, "statut", "statutPartant") or "").upper() in ("NON_PARTANT", "NP")
    return {
        "numero": number, "nom": name, "non_partant": np,
        "musique": first(raw, "musique", "musiqueCheval"),
        "jockey": text(first(raw, "nomJockey", "jockey")),
        "driver": text(first(raw, "nomDriver", "driver")),
        "entraineur": text(first(raw, "nomEntraineur", "entraineur")),
        "age": integer(first(raw, "age")),
        "sexe": text(first(raw, "sexe")),
    }


def extract_meetings(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("programme", "reunions", "réunions"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            meetings = value.get("reunions")
            if isinstance(meetings, list):
                return meetings
    return []


def extract_races(reunion):
    value = first(reunion, "courses", "listeCourses")
    return value if isinstance(value, list) else []


def extract_runners(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("participants", "partants"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def collect(day):
    errors, warnings, races = [], [], []
    raw = get_json("programme/" + day.strftime("%d%m%Y"))
    meetings = extract_meetings(raw)
    if not meetings:
        warnings.append("Aucune réunion lisible dans la réponse PMU")
    for meeting in meetings:
        if not isinstance(meeting, dict):
            continue
        r = integer(first(meeting, "numOfficiel", "numReunion", "numero"))
        if not r:
            warnings.append("Réunion sans numéro ignorée")
            continue
        country = location_france(meeting)
        if country is not True:
            warnings.append("R%d ignorée : localisation française non confirmée" % r)
            continue
        track = first(meeting, "hippodrome", "libelleCourt", "libelleLong")
        if isinstance(track, dict):
            track = first(track, "libelleCourt", "libelleLong", "nom")
        for race in extract_races(meeting):
            if not isinstance(race, dict):
                continue
            c = integer(first(race, "numOrdre", "numCourse", "numero"))
            if not c:
                continue
            cid = "%s-R%d-C%d" % (day.isoformat(), r, c)
            try:
                details = get_json("programme/%s/R%d/C%d/participants" % (day.strftime("%d%m%Y"), r, c))
                source_runners = extract_runners(details)
                runners, seen = [], set()
                for entry in source_runners:
                    item = normalize_runner(entry)
                    if item is None:
                        continue
                    if item["numero"] in seen:
                        raise ValueError("Numéro de partant dupliqué")
                    seen.add(item["numero"])
                    runners.append(item)
                if not runners:
                    warnings.append(cid + " : aucun partant exploitable")
                    continue
                departure = paris_depart(first(race, "heureDepart", "depart", "dateDepart"))
                if not departure:
                    warnings.append(cid + " : départ non vérifiable")
                    continue
                if dt.datetime.fromisoformat(departure).date() != day:
                    warnings.append(cid + " : date de départ incohérente")
                    continue
                races.append({
                    "id": cid, "reunion": r, "course": c, "hippodrome": text(track),
                    "nom": text(first(race, "libelle", "nom", "libelleCourt")),
                    "depart": departure, "localisation_france_verifiee": True,
                    "source": API + "/programme/" + day.strftime("%d%m%Y"),
                    "source_verifiee_a": now().isoformat(),
                    "partants": runners, "nombre_partants": sum(not p["non_partant"] for p in runners),
                    "non_partants": [p["numero"] for p in runners if p["non_partant"]],
                    "statut": "COLLECTE_REELLE_NON_IMPORTEE",
                })
            except (RuntimeError, ValueError, TypeError) as exc:
                errors.append(cid + " : " + str(exc))
    return {"version": "M1-PMU-COLLECTE-1.0", "date": day.isoformat(), "collecte_a": now().isoformat(),
            "source": API, "statut": "PARTIEL" if errors else ("OK" if races else "AUCUNE_COURSE_VERIFIEE"),
            "courses": sorted(races, key=lambda x: x["depart"]), "erreurs": errors,
            "avertissements": warnings,
            "statistiques": {"reunions_recues": len(meetings), "courses": len(races),
                             "chevaux": sum(len(c["partants"]) for c in races),
                             "non_partants": sum(len(c["non_partants"]) for c in races)}}


def write_output(name, payload):
    if name not in OUTPUTS:
        raise ValueError("Sortie non autorisée")
    DATA.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".pmu-m1-", dir=DATA)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, DATA / name)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def self_test():
    assert integer("7") == 7
    assert integer(True) is None
    assert location_france({"codePays": "FRA"}) is True
    assert location_france({"hippodrome": "PARIS"}) is None
    assert location_france({"codePays": "BEL"}) is False
    assert normalize_runner({"numPmu": 3, "nom": "TEST", "nonPartant": True})["non_partant"] is True
    assert paris_depart(0) is None
    assert extract_meetings({"programme": {"reunions": [{"numOfficiel": 1}]}})
    print("SELF-TEST OK (sans réseau et sans écriture)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Date de course YYYY-MM-DD, heure de Paris")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Aucune écriture")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        day = dt.date.fromisoformat(args.date) if args.date else now().date()
        result = collect(day)
    except (ValueError, RuntimeError, urllib.error.URLError) as exc:
        print("COLLECTE EN ECHEC :", exc)
        return 1
    diagnostic = {key: result[key] for key in ("version", "date", "collecte_a", "statut", "statistiques", "erreurs", "avertissements")}
    if not args.dry_run:
        write_output("collecte_m1_pmu.json", result)
        write_output("diagnostic_collecte_m1.json", diagnostic)
    print(json.dumps(diagnostic, ensure_ascii=False, indent=2))
    return 0 if result["statut"] == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
