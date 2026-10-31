#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🇸🇳🧠🔥 X1000 M1 — COLLECTEUR PMU PROTÉGÉ
Version M1-PMU-COLLECTE-2.0

Bibliothèque standard Python uniquement.

COMMANDES :
    python pmu_engine.py --self-test
    python pmu_engine.py --date 2026-10-31
    python pmu_engine.py --dry-run

SORTIES EXCLUSIVES :
    data/collecte_m1_pmu.json
    data/diagnostic_collecte_m1.json

PROTECTIONS :
- Aucun import automatique dans m1.py
- Aucune écriture sur courses.json
- Aucune écriture sur selections.json
- Aucune modification des pronostics
- Aucune modification des FIGÉs historiques
- Aucune modification des audits
- Aucune donnée hippique inventée
- Aucune réussite fictive
"""

import argparse
import datetime as dt
import json
import math
import os
import random
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request

from pathlib import Path
from zoneinfo import ZoneInfo


VERSION = "M1-PMU-COLLECTE-2.0"

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

PARIS = ZoneInfo("Europe/Paris")

API = os.environ.get(
    "PMU_API_BASE",
    "https://online.turfinfo.api.pmu.fr/rest/client/7"
).rstrip("/")

OUTPUTS = frozenset({
    "collecte_m1_pmu.json",
    "diagnostic_collecte_m1.json"
})

TIMEOUT = 15
ATTEMPTS = 3
MAX_BYTES = 8_000_000

RETRY_HTTP = {
    408, 429, 500, 502, 503, 504
}

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; X1000-M1-PMU/2.0)"
)


# ============================================
# HORLOGE
# ============================================

def now():
    return dt.datetime.now(PARIS)


# ============================================
# JSON STRICT
# ============================================

def reject_constant(value):
    raise ValueError(
        "Constante JSON interdite : " + value
    )


def reject_duplicates(pairs):
    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError(
                "Clé JSON dupliquée : " + key
            )
        result[key] = value

    return result


def decode_json(content):
    return json.loads(
        content.decode("utf-8-sig"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates
    )


# ============================================
# OUTILS DE NORMALISATION
# ============================================

def first(obj, *keys):
    if not isinstance(obj, dict):
        return None

    for key in keys:
        value = obj.get(key)

        if value is not None and value != "":
            return value

    return None


def integer(value):
    if value is None or isinstance(value, bool):
        return None

    try:
        if isinstance(value, str):
            value = value.strip()

            if not re.fullmatch(r"\d+", value):
                return None

        if isinstance(value, float):
            if (
                not math.isfinite(value)
                or not value.is_integer()
            ):
                return None

        result = int(value)

        return result if result > 0 else None

    except (
        TypeError,
        ValueError,
        OverflowError
    ):
        return None


def text(value):
    if isinstance(value, str):
        return value.strip() or None

    if isinstance(value, dict):
        return text(first(
            value,
            "nom",
            "libelleLong",
            "libelleCourt",
            "libelle",
            "name"
        ))

    return None


def music(value):
    if isinstance(value, str):
        return value.strip()

    if isinstance(value, list):
        return " ".join(
            str(item)
            for item in value
            if item is not None
        )

    return None


# ============================================
# HEURE DE PARIS
# ============================================

def paris_depart(value, day=None):
    if value is None or isinstance(value, bool):
        return None

    try:
        if isinstance(value, (int, float)):
            number = float(value)

            if (
                not math.isfinite(number)
                or number <= 0
            ):
                return None

            if number > 100_000_000_000:
                number /= 1000

            return dt.datetime.fromtimestamp(
                number,
                PARIS
            ).isoformat()

        if isinstance(value, str):
            value = value.strip()

            if value.isdigit():
                return paris_depart(
                    int(value),
                    day
                )

            try:
                parsed = dt.datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                )

                if parsed.tzinfo is not None:
                    return parsed.astimezone(
                        PARIS
                    ).isoformat()

            except ValueError:
                pass

            if day is not None:
                try:
                    local_time = dt.time.fromisoformat(
                        value
                    )

                    if local_time.tzinfo is not None:
                        return None

                    return dt.datetime.combine(
                        day,
                        local_time,
                        tzinfo=PARIS
                    ).isoformat()

                except ValueError:
                    pass

    except (
        ValueError,
        OverflowError,
        OSError,
        TypeError
    ):
        pass

    return None


# ============================================
# REQUÊTES PMU
# ============================================

def get_json(path):
    url = API + "/" + path.lstrip("/")
    last_error = None

    for attempt in range(ATTEMPTS):
        wait = 0

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Language": "fr-FR,fr;q=0.9"
            }
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=TIMEOUT
            ) as response:

                if response.status != 200:
                    raise RuntimeError(
                        "HTTP " + str(response.status)
                    )

                content = response.read(
                    MAX_BYTES + 1
                )

                if not content:
                    raise ValueError(
                        "Réponse PMU vide"
                    )

                if len(content) > MAX_BYTES:
                    raise ValueError(
                        "Réponse PMU trop volumineuse"
                    )

                return decode_json(content)

        except urllib.error.HTTPError as exc:
            last_error = exc

            if exc.code not in RETRY_HTTP:
                raise RuntimeError(
                    f"HTTP {exc.code} : {url}"
                ) from exc

            if exc.code == 429:
                retry_after = exc.headers.get(
                    "Retry-After"
                )

                if retry_after:
                    try:
                        wait = min(
                            30,
                            max(0, int(retry_after))
                        )
                    except ValueError:
                        pass

        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ConnectionError,
            OSError,
            ValueError,
            RuntimeError,
            UnicodeError
        ) as exc:
            last_error = exc

        if attempt < ATTEMPTS - 1:
            delay = min(
                15,
                2 ** attempt
                + random.uniform(0, 0.5)
            )

            time.sleep(max(delay, wait))

    raise RuntimeError(
        f"Échec PMU {path} après "
        f"{ATTEMPTS} tentatives : {last_error}"
    )


# ============================================
# CONTRÔLE DE LA FRANCE
# ============================================

def country_code(value):
    if isinstance(value, dict):
        value = first(
            value,
            "code",
            "codePays",
            "nom",
            "libelle"
        )

    if not isinstance(value, str):
        return None

    value = value.strip().upper()

    if value in ("FR", "FRA", "FRANCE"):
        return True

    if value:
        return False

    return None


def location_france(meeting):
    if not isinstance(meeting, dict):
        return None

    values = []

    for key in (
        "codePays",
        "pays",
        "paysCode",
        "countryCode"
    ):
        if key in meeting:
            values.append(meeting[key])

    track = meeting.get("hippodrome")

    if isinstance(track, dict):
        for key in (
            "codePays",
            "pays",
            "paysCode",
            "countryCode"
        ):
            if key in track:
                values.append(track[key])

    checks = [
        country_code(value)
        for value in values
    ]

    if False in checks:
        return False

    if True in checks:
        return True

    return None


# ============================================
# PARTICIPANTS
# ============================================

def normalize_runner(raw):
    if not isinstance(raw, dict):
        raise ValueError(
            "Participant non exploitable"
        )

    number = integer(first(
        raw,
        "numPmu",
        "numero",
        "num",
        "numeroPmu"
    ))

    name = text(first(
        raw,
        "nom",
        "nomCheval",
        "name"
    ))

    if number is None or name is None:
        raise ValueError(
            "Numéro ou nom de cheval absent"
        )

    status = text(first(
        raw,
        "statut",
        "statutPartant"
    ))

    normalized_status = (
        status or ""
    ).upper().replace(
        "-", "_"
    ).replace(
        " ", "_"
    )

    non_runner = (
        first(
            raw,
            "nonPartant",
            "non_partant"
        ) is True
        or normalized_status in (
            "NON_PARTANT",
            "NONPARTANT",
            "NP",
            "FORFAIT"
        )
    )

    return {
        "numero": number,
        "nom": name,
        "non_partant": non_runner,
        "statut": status or "INCONNU",
        "musique": music(first(
            raw,
            "musique",
            "musiqueCheval"
        )),
        "jockey": text(first(
            raw,
            "nomJockey",
            "jockey"
        )),
        "driver": text(first(
            raw,
            "nomDriver",
            "driver"
        )),
        "entraineur": text(first(
            raw,
            "nomEntraineur",
            "entraineur"
        )),
        "age": integer(
            raw.get("age")
        ),
        "sexe": text(
            raw.get("sexe")
        )
    }


# ============================================
# STRUCTURES PMU
# ============================================

def extract_meetings(payload):
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        raise ValueError(
            "Programme PMU invalide"
        )

    programme = payload.get(
        "programme",
        payload
    )

    if isinstance(programme, list):
        return programme

    if isinstance(programme, dict):
        meetings = first(
            programme,
            "reunions",
            "réunions"
        )

        if isinstance(meetings, list):
            return meetings

    raise ValueError(
        "Liste des réunions absente"
    )


def extract_races(meeting):
    races = first(
        meeting,
        "courses",
        "listeCourses"
    )

    if not isinstance(races, list):
        raise ValueError(
            "Liste des courses absente"
        )

    return races


def extract_runners(payload):
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        runners = first(
            payload,
            "participants",
            "partants"
        )

        if isinstance(runners, list):
            return runners

    raise ValueError(
        "Liste des participants absente"
    )


# ============================================
# COLLECTE PRINCIPALE
# ============================================

def collect(day):
    if type(day) is not dt.date:
        raise ValueError(
            "Date de collecte invalide"
        )

    started = now()
    date_api = day.strftime("%d%m%Y")

    programme_path = (
        "programme/" + date_api
    )

    programme_url = (
        API + "/" + programme_path
    )

    report = {
        "version": VERSION,
        "date": day.isoformat(),
        "collecte_a": started.isoformat(),
        "source": programme_url,
        "statut": "EN_COURS",
        "courses": [],
        "erreurs": [],
        "avertissements": [],
        "statistiques": {}
    }

    errors = report["erreurs"]
    warnings = report["avertissements"]
    races = report["courses"]

    raw = get_json(programme_path)
    meetings = extract_meetings(raw)

    valid_meetings = 0
    expected_races = 0
    seen_ids = set()

    if not meetings:
        warnings.append(
            "Aucune réunion reçue"
        )

    for meeting in meetings:
        if not isinstance(meeting, dict):
            errors.append(
                "Réunion : objet invalide"
            )
            continue

        r = integer(first(
            meeting,
            "numOfficiel",
            "numReunion",
            "numero"
        ))

        if r is None:
            errors.append(
                "Réunion sans numéro valide"
            )
            continue

        country = location_france(meeting)

        if country is not True:
            warnings.append(
                f"R{r} ignorée : "
                "localisation française non confirmée"
            )
            continue

        valid_meetings += 1

        track = text(first(
            meeting,
            "hippodrome",
            "libelleCourt",
            "libelleLong"
        ))

        try:
            meeting_races = extract_races(meeting)

        except ValueError as exc:
            errors.append(
                f"R{r} : {exc}"
            )
            continue

        for race in meeting_races:
            if not isinstance(race, dict):
                errors.append(
                    f"R{r} : course invalide"
                )
                continue

            c = integer(first(
                race,
                "numOrdre",
                "numCourse",
                "numero"
            ))

            if c is None:
                errors.append(
                    f"R{r} : numéro de course absent"
                )
                continue

            cid = (
                f"{day.isoformat()}-R{r}-C{c}"
            )

            if cid in seen_ids:
                errors.append(
                    f"{cid} : course dupliquée"
                )
                continue

            seen_ids.add(cid)
            expected_races += 1

            participants_path = (
                f"programme/{date_api}/"
                f"R{r}/C{c}/participants"
            )

            try:
                details = get_json(
                    participants_path
                )

                source_runners = extract_runners(
                    details
                )

                if not source_runners:
                    raise ValueError(
                        "Aucun participant reçu"
                    )

                runners = []
                seen_numbers = set()

                for entry in source_runners:
                    item = normalize_runner(entry)

                    number = item["numero"]

                    if number in seen_numbers:
                        raise ValueError(
                            f"Numéro {number} dupliqué"
                        )

                    seen_numbers.add(number)
                    runners.append(item)

                departure = paris_depart(
                    first(
                        race,
                        "heureDepart",
                        "heureDepartPrevue",
                        "depart",
                        "dateDepart"
                    ),
                    day
                )

                if departure is None:
                    raise ValueError(
                        "Heure de départ non vérifiable"
                    )

                if (
                    dt.datetime.fromisoformat(
                        departure
                    ).date() != day
                ):
                    raise ValueError(
                        "Date de départ incohérente"
                    )

                runners.sort(
                    key=lambda p: p["numero"]
                )

                non_runners = [
                    p["numero"]
                    for p in runners
                    if p["non_partant"]
                ]

                received = now().isoformat()

                races.append({
                    "id": cid,
                    "course_id": cid,
                    "reunion": r,
                    "course": c,
                    "hippodrome": track,
                    "nom": text(first(
                        race,
                        "libelle",
                        "nom",
                        "libelleCourt"
                    )),
                    "depart": departure,
                    "localisation_france_verifiee": True,
                    "source": (
                        API + "/" + participants_path
                    ),
                    "source_verifiee_a": received,
                    "collecte_a": received,
                    "partants": runners,
                    "nombre_participants": len(runners),
                    "nombre_partants": (
                        len(runners)
                        - len(non_runners)
                    ),
                    "non_partants": non_runners,
                    "statut": (
                        "COLLECTE_REELLE_NON_IMPORTEE"
                    )
                })

                time.sleep(0.15)

            except (
                RuntimeError,
                ValueError,
                TypeError,
                OSError
            ) as exc:
                errors.append(
                    f"{cid} : {exc}"
                )

    races.sort(
        key=lambda x: (
            x["depart"],
            x["reunion"],
            x["course"]
        )
    )

    report["statistiques"] = {
        "reunions_recues": len(meetings),
        "reunions_francaises_verifiees": valid_meetings,
        "courses_prevues": expected_races,
        "courses": len(races),
        "chevaux": sum(
            len(c["partants"])
            for c in races
        ),
        "partants": sum(
            c["nombre_partants"]
            for c in races
        ),
        "non_partants": sum(
            len(c["non_partants"])
            for c in races
        ),
        "erreurs": len(errors)
    }

    if races:
        report["statut"] = (
            "PARTIEL"
            if errors or len(races) < expected_races
            else "OK"
        )

    elif errors:
        report["statut"] = "ECHEC"

    else:
        report["statut"] = (
            "AUCUNE_COURSE_VERIFIEE"
        )

    return report


# ============================================
# ÉCRITURE ATOMIQUE PROTÉGÉE
# ============================================

def write_output(name, payload):
    if name not in OUTPUTS:
        raise ValueError(
            "Sortie non autorisée"
        )

    DATA.mkdir(
        parents=True,
        exist_ok=True
    )

    destination = DATA / name

    fd, temporary = tempfile.mkstemp(
        prefix=".pmu-m1-",
        suffix=".tmp",
        dir=DATA
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8"
        ) as stream:

            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                indent=2,
                allow_nan=False
            )

            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(
            temporary,
            destination
        )

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# ============================================
# TESTS SANS RÉSEAU
# ============================================

def self_test():
    assert integer("7") == 7
    assert integer(True) is None
    assert integer("7.5") is None

    assert location_france({
        "codePays": "FRA"
    }) is True

    assert location_france({
        "hippodrome": "PARIS"
    }) is None

    assert location_france({
        "codePays": "BEL"
    }) is False

    assert location_france({
        "hippodrome": {
            "codePays": "FR"
        }
    }) is True

    runner = normalize_runner({
        "numPmu": 3,
        "nom": "CHEVAL TEST",
        "nonPartant": True
    })

    assert runner["non_partant"] is True

    assert paris_depart(0) is None

    assert extract_meetings({
        "programme": {
            "reunions": [
                {"numOfficiel": 1}
            ]
        }
    })

    assert extract_runners({
        "participants": [
            {"numPmu": 1, "nom": "TEST"}
        ]
    })

    try:
        normalize_runner({
            "numPmu": None,
            "nom": "TEST"
        })
        raise AssertionError(
            "Participant invalide accepté"
        )
    except ValueError:
        pass

    print("=" * 50)
    print("🇸🇳🧠🔥 X1000 M1 — COLLECTE PMU")
    print("VERSION :", VERSION)
    print("SELF-TEST OK")
    print("JSON : OK")
    print("Numéros : OK")
    print("France : OK")
    print("Participants : OK")
    print("Heure Paris : OK")
    print("Aucune écriture sur M1")
    print("=" * 50)


# ============================================
# EXECUTION
# ============================================

def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--date",
        help="Date YYYY-MM-DD, heure Paris"
    )

    parser.add_argument(
        "--self-test",
        action="store_true"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Aucune écriture"
    )

    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    try:
        day = (
            dt.date.fromisoformat(args.date)
            if args.date
            else now().date()
        )

        result = collect(day)

    except (
        ValueError,
        RuntimeError,
        urllib.error.URLError,
        OSError
    ) as exc:
        print(
            "COLLECTE EN ÉCHEC :",
            exc,
            file=sys.stderr
        )
        return 1

    diagnostic = {
        key: result[key]
        for key in (
            "version",
            "date",
            "collecte_a",
            "statut",
            "statistiques",
            "erreurs",
            "avertissements"
        )
    }

    if not args.dry_run:
        try:
            # Le diagnostic est toujours conservé.
            write_output(
                "diagnostic_collecte_m1.json",
                diagnostic
            )

            # Préserver la dernière collecte
            # exploitable en cas d'échec.
            if result["courses"]:
                write_output(
                    "collecte_m1_pmu.json",
                    result
                )

        except OSError as exc:
            print(
                "ÉCHEC ÉCRITURE :",
                exc,
                file=sys.stderr
            )
            return 1

    print(json.dumps(
        diagnostic,
        ensure_ascii=False,
        indent=2
    ))

    if result["statut"] == "OK":
        return 0

    if result["statut"] == "PARTIEL":
        return 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
