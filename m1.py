#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🇸🇳🧠🔥 MPAA X1000 — M1 INDÉPENDANT
Version consolidée 6.0

Bibliothèque standard Python uniquement.

MISSIONS
- Importer des courses PMU vérifiées en France
- Préserver l'autonomie sportive de M1
- Enregistrer les sélections M1
- FIGER exclusivement avant le départ
- Conserver les FIGÉs historiques
- Auditer après course avec résultat attesté
- Protéger les fichiers JSON
- Détecter les erreurs et les doublons
- Servir les données en lecture seule

AUCUNE DONNÉE HIPPIQUE INVENTÉE.
AUCUN PRONOSTIC AUTOMATIQUE FICTIF.
AUCUN FIGÉ RÉTROACTIF.
AUCUN RÉSULTAT OFFICIEL INVENTÉ.

Commandes :

python m1.py --self-test

python m1.py --import-json data/collecte.json

python m1.py --select 2026-10-31-R1-C1 \
  --ranking 9,6,8,4,7 \
  --analysis "Justification sportive documentée." 

python m1.py --lock 2026-10-31-R1-C1 \
  --verification-json controles.json

python m1.py --audit 2026-10-31-R1-C1 \
  --arrival 9,6,8,4,7 \
  --source-officielle "Référence officielle" \
  --resultat-verifie

python m1.py --verify-archives
python m1.py --serve

IMPORTANT :
Une attestation d'opérateur n'est pas une
vérification indépendante des données officielles.
SHA256 détecte les altérations, mais ne constitue
pas une signature cryptographique authentifiée.
"""

import argparse
import datetime as dt
import hashlib
import hmac
import json
import math
import os
import re
import sys
import tempfile
import threading
import uuid

from pathlib import Path
from zoneinfo import ZoneInfo
from http.server import (
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer
)
from urllib.parse import urlsplit


# ============================================
# CONFIGURATION
# ============================================

VERSION = "M1-6.0"

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
WEB = ROOT / "web"

PARIS = ZoneInfo("Europe/Paris")

LOCK = threading.RLock()

COURSE_ID = re.compile(
    r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$"
)

ALLOWED = frozenset({
    "courses.json",
    "selections.json",
    "figes.json",
    "audits.json",
    "import_diagnostic.json"
})

FORMULES = {
    "Simple gagnant": (1, 1),
    "Simple placé": (1, 1),
    "Couplé gagnant": (2, 2),
    "Couplé placé": (2, 2),
    "2sur4": (2, 2),
    "Trio": (3, 3),
    "Multi": (4, 7),
    "Tiercé": (3, 3),
    "Quarté+": (4, 4),
    "Quinté+": (5, 5),
    "Pick5": (5, 5),
    "Super4": (4, 4)
}

# Bornes indicatives.
# Les conditions réelles PMU doivent être confirmées.


# ============================================
# HORLOGE
# ============================================

def now():
    return dt.datetime.now(PARIS)


def parse_date(value):
    if not isinstance(value, str):
        raise ValueError(
            "Date ISO avec fuseau requise"
        )

    try:
        value = value.replace("Z", "+00:00")
        result = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            "Date ISO invalide"
        ) from exc

    if (
        result.tzinfo is None
        or result.utcoffset() is None
    ):
        raise ValueError(
            "Fuseau horaire absent"
        )

    return result.astimezone(PARIS)


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


def load_json(text):
    return json.loads(
        text,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates
    )


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(
        canonical(value)
    ).hexdigest()


# ============================================
# FICHIERS AUTORISÉS
# ============================================

def path_for(name):
    if name not in ALLOWED:
        raise ValueError(
            "Fichier non autorisé"
        )

    return DATA / name


def read(name, fallback):
    path = path_for(name)

    if not path.exists():
        return fallback

    return load_json(
        path.read_text(encoding="utf-8")
    )


def write(name, payload):
    path = path_for(name)

    DATA.mkdir(
        parents=True,
        exist_ok=True
    )

    fd, temporary = tempfile.mkstemp(
        prefix=".m1-",
        suffix=".tmp",
        dir=DATA
    )

    try:
        with os.fdopen(
            fd, "w", encoding="utf-8"
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

        os.replace(temporary, path)

        try:
            directory_fd = os.open(
                DATA, os.O_RDONLY
            )

            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

        except OSError:
            pass

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def records(name):
    value = read(name, [])

    if not isinstance(value, list):
        raise ValueError(
            f"{name} : liste attendue"
        )

    if any(
        not isinstance(x, dict)
        for x in value
    ):
        raise ValueError(
            f"{name} : objet invalide"
        )

    return value


# ============================================
# NUMÉROS
# ============================================

def numbers(value, label, allow_empty=False):
    if not isinstance(value, list):
        raise ValueError(
            f"{label} : liste requise"
        )

    if not value and not allow_empty:
        raise ValueError(
            f"{label} : liste vide"
        )

    if any(
        type(n) is not int or n <= 0
        for n in value
    ):
        raise ValueError(
            f"{label} : numéros invalides"
        )

    if len(value) != len(set(value)):
        raise ValueError(
            f"{label} : doublons interdits"
        )

    return value


def parse_csv(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "Liste de numéros absente"
        )

    try:
        values = [
            int(x.strip())
            for x in value.split(",")
        ]

        return numbers(values, "Numéros")

    except ValueError as exc:
        raise ValueError(
            "Liste de numéros invalide : " + str(exc)
        ) from exc


# ============================================
# NORMALISATION DES COURSES
# ============================================

def normalize_course(raw):
    if not isinstance(raw, dict):
        raise ValueError(
            "Course : objet attendu"
        )

    cid = raw.get(
        "id", raw.get("course_id")
    )

    if (
        not isinstance(cid, str)
        or not COURSE_ID.fullmatch(cid)
    ):
        raise ValueError(
            "Identifiant de course invalide"
        )

    try:
        dt.date.fromisoformat(cid[:10])
    except ValueError as exc:
        raise ValueError(
            "Date de course invalide"
        ) from exc

    if raw.get(
        "localisation_france_verifiee"
    ) is not True:
        raise ValueError(
            "Localisation France non vérifiée"
        )

    depart = parse_date(
        raw.get("depart")
    )

    if depart.date().isoformat() != cid[:10]:
        raise ValueError(
            "Date de départ incohérente"
        )

    raw_partants = raw.get("partants")

    if (
        not isinstance(raw_partants, list)
        or not raw_partants
    ):
        raise ValueError(
            "Liste des partants absente"
        )

    cleaned = []
    seen = set()

    for p in raw_partants:
        if not isinstance(p, dict):
            raise ValueError(
                "Partant invalide"
            )

        n = p.get("numero")

        if (
            type(n) is not int
            or n <= 0
            or n in seen
        ):
            raise ValueError(
                "Numéro absent, invalide ou dupliqué"
            )

        seen.add(n)

        name = p.get("nom")

        if (
            not isinstance(name, str)
            or not name.strip()
        ):
            raise ValueError(
                "Nom de cheval absent"
            )

        np = p.get("non_partant", False)

        if type(np) is not bool:
            raise ValueError(
                "non_partant doit être booléen"
            )

        cleaned.append({
            "numero": n,
            "nom": name.strip(),
            "non_partant": np,
            "statut": p.get("statut"),
            "musique": p.get("musique"),
            "jockey": p.get("jockey"),
            "driver": p.get("driver"),
            "entraineur": p.get("entraineur"),
            "ferrure": p.get("ferrure"),
            "observations": p.get("observations"),
            "age": p.get("age"),
            "sexe": p.get("sexe"),
            "gains": p.get("gains")
        })

    if len([
        p for p in cleaned
        if not p["non_partant"]
    ]) < 5:
        raise ValueError(
            "Moins de cinq partants actifs"
        )

    if "nombre_partants" in raw:
        expected = raw["nombre_partants"]

        if (
            type(expected) is not int
            or expected != sum(
                not p["non_partant"]
                for p in cleaned
            )
        ):
            raise ValueError(
                "Nombre de partants incohérent"
            )

    if "non_partants" in raw:
        expected_np = numbers(
            raw["non_partants"],
            "Non-partants",
            allow_empty=True
        )

        actual_np = [
            p["numero"]
            for p in cleaned
            if p["non_partant"]
        ]

        if sorted(expected_np) != sorted(actual_np):
            raise ValueError(
                "Non-partants incohérents"
            )

    forms = raw.get("formules", [])

    if not isinstance(forms, list):
        raise ValueError(
            "Formules : liste attendue"
        )

    if any(
        not isinstance(f, str)
        for f in forms
    ):
        raise ValueError(
            "Formule invalide"
        )

    if len(forms) != len(set(forms)):
        raise ValueError(
            "Formules dupliquées"
        )

    if any(
        f not in FORMULES
        for f in forms
    ):
        raise ValueError(
            "Formule inconnue"
        )

    checked_at = raw.get(
        "source_verifiee_a"
    )

    if checked_at is not None:
        parse_date(checked_at)

    collecte_a = raw.get("collecte_a")

    if collecte_a is not None:
        parse_date(collecte_a)

    return {
        "id": cid,
        "course_id": cid,
        "localisation_france_verifiee": True,
        "hippodrome": raw.get("hippodrome"),
        "nom": raw.get("nom"),
        "discipline": raw.get("discipline"),
        "depart": depart.isoformat(),
        "source": raw.get("source"),
        "source_verifiee_a": checked_at,
        "collecte_a": collecte_a,
        "formules": forms,
        "partants": cleaned
    }


def starters(course):
    return {
        p["numero"]
        for p in course["partants"]
        if not p["non_partant"]
    }


# ============================================
# VALIDATION DES PARIS
# ============================================

def validate_bet(bet, course):
    if not isinstance(bet, dict):
        raise ValueError(
            "Pari : objet attendu"
        )

    allowed = {
        "formule",
        "numeros",
        "mise",
        "note"
    }

    if set(bet) - allowed:
        raise ValueError(
            "Pari : champs inconnus"
        )

    formula = bet.get("formule")

    if (
        not isinstance(formula, str)
        or formula not in FORMULES
        or formula not in course["formules"]
    ):
        raise ValueError(
            "Formule non attestée comme proposée"
        )

    nums = numbers(
        bet.get("numeros"),
        "Pari"
    )

    low, high = FORMULES[formula]

    if not low <= len(nums) <= high:
        raise ValueError(
            "Nombre de chevaux incompatible"
        )

    if not set(nums) <= starters(course):
        raise ValueError(
            "Cheval absent ou non-partant"
        )

    if "mise" in bet:
        mise = bet["mise"]

        if (
            type(mise) not in (int, float)
            or not math.isfinite(mise)
            or not 0 < mise < 100000
        ):
            raise ValueError(
                "Mise invalide"
            )

    if (
        "note" in bet
        and not isinstance(bet["note"], str)
    ):
        raise ValueError(
            "Note de pari invalide"
        )

    return bet


# ============================================
# INTÉGRITÉ DES FIGÉS
# ============================================

def verify_archives(archives):
    if not isinstance(archives, list):
        raise ValueError(
            "Archives FIGÉs invalides"
        )

    versions = {}
    last_times = {}
    seen_selections = set()

    for index, record in enumerate(archives):
        if not isinstance(record, dict):
            raise ValueError(
                f"FIGÉ {index} invalide"
            )

        given = record.get("sha256")

        body = {
            k: v
            for k, v in record.items()
            if k != "sha256"
        }

        if (
            not isinstance(given, str)
            or not hmac.compare_digest(
                given, digest(body)
            )
        ):
            raise ValueError(
                f"FIGÉ {index} : SHA256 invalide"
            )

        cid = record.get("course_id")
        version = record.get("version")

        if (
            not isinstance(cid, str)
            or not COURSE_ID.fullmatch(cid)
        ):
            raise ValueError(
                "FIGÉ : identifiant invalide"
            )

        if (
            type(version) is not int
            or version != versions.get(cid, 0) + 1
        ):
            raise ValueError(
                "Séquence des versions invalide"
            )

        if record.get("moteur") != "M1":
            raise ValueError(
                "FIGÉ étranger à M1"
            )

        if record.get("statut") != "FIGE_AVANT_DEPART":
            raise ValueError(
                "Statut FIGÉ invalide"
            )

        moment = parse_date(
            record.get("fige_a")
        )

        depart = parse_date(
            record.get("depart")
        )

        if (
            moment >= depart
            or depart.date().isoformat() != cid[:10]
        ):
            raise ValueError(
                "FIGÉ rétroactif ou incohérent"
            )

        if (
            cid in last_times
            and last_times[cid] >= moment
        ):
            raise ValueError(
                "Versions non chronologiques"
            )

        ranking = numbers(
            record.get("classement"),
            "Classement FIGÉ"
        )

        if len(ranking) != 5:
            raise ValueError(
                "Cinq positions FIGÉes requises"
            )

        sid = record.get("selection_id")

        if sid is not None:
            if (
                not isinstance(sid, str)
                or not sid
                or (cid, sid) in seen_selections
            ):
                raise ValueError(
                    "Sélection FIGÉe dupliquée"
                )

            seen_selections.add((cid, sid))

        if record.get("selection_creee_a"):
            created = parse_date(
                record["selection_creee_a"]
            )

            if created > moment:
                raise ValueError(
                    "Sélection créée après FIGÉ"
                )

        versions[cid] = version
        last_times[cid] = moment

    return len(archives)


# ============================================
# IMPORTATION DES COURSES
# ============================================

def import_courses(filename):
    payload = load_json(
        Path(filename).read_text(
            encoding="utf-8"
        )
    )

    if isinstance(payload, list):
        source = payload

    elif isinstance(payload, dict):
        source = payload.get("courses")

        if payload.get("statut") == "ECHEC":
            raise ValueError(
                "Collecte PMU en échec"
            )

    else:
        source = None

    if not isinstance(source, list):
        raise ValueError(
            "Liste courses[] absente"
        )

    valid = {}
    rejected = []
    encountered = set()

    for raw in source:
        try:
            course = normalize_course(raw)
            cid = course["id"]

            if cid in encountered:
                valid.pop(cid, None)
                raise ValueError(
                    "Course dupliquée"
                )

            encountered.add(cid)
            valid[cid] = course

        except (
            ValueError,
            TypeError,
            KeyError
        ) as exc:

            rejected.append({
                "id": (
                    raw.get("id")
                    if isinstance(raw, dict)
                    else None
                ),
                "erreur": str(exc)
            })

    with LOCK:
        archives = records("figes.json")
        verify_archives(archives)

        locked = {
            f["course_id"]
            for f in archives
        }

        existing_list = records("courses.json")

        existing = {
            c["id"]: c
            for c in existing_list
        }

        if len(existing) != len(existing_list):
            raise ValueError(
                "Courses existantes dupliquées"
            )

        imported = 0

        for cid, course in valid.items():
            if (
                cid in locked
                and existing.get(cid) != course
            ):
                rejected.append({
                    "id": cid,
                    "erreur": (
                        "Course FIGÉe : "
                        "modification interdite"
                    )
                })
                continue

            existing[cid] = course
            imported += 1

        if imported:
            write(
                "courses.json",
                sorted(
                    existing.values(),
                    key=lambda c: c["depart"]
                )
            )

        report = {
            "date": now().isoformat(),
            "version": VERSION,
            "importees": imported,
            "rejetees": rejected,
            "total_courses": len(existing)
        }

        write(
            "import_diagnostic.json",
            report
        )

        return report


# ============================================
# RECHERCHE D'UNE COURSE
# ============================================

def course_by_id(cid):
    matches = [
        c for c in records("courses.json")
        if c.get("id") == cid
    ]

    if len(matches) > 1:
        raise ValueError(
            "Course dupliquée en base"
        )

    return matches[0] if matches else None


# ============================================
# DÉCISION SPORTIVE M1
# ============================================

def create_selection(cid, ranking, analysis, bet=None):
    with LOCK:
        course = course_by_id(cid)

        if course is None:
            raise ValueError(
                "Course inconnue"
            )

        moment = now()
        depart = parse_date(course["depart"])

        if moment >= depart:
            raise ValueError(
                "Sélection après départ interdite"
            )

        numbers(ranking, "Classement")

        if len(ranking) != 5:
            raise ValueError(
                "M1 exige cinq positions"
            )

        if not set(ranking) <= starters(course):
            raise ValueError(
                "Cheval absent ou non-partant"
            )

        if (
            not isinstance(analysis, str)
            or len(analysis.strip()) < 30
        ):
            raise ValueError(
                "Justification sportive insuffisante"
            )

        if bet is not None:
            validate_bet(bet, course)

        selections = records("selections.json")

        record = {
            "course_id": cid,
            "moteur": "M1",
            "classement": list(ranking),
            "analyse": analysis.strip(),
            "pari_envisage": bet,
            "statut": "PROVISOIRE",
            "cree_a": moment.isoformat(),
            "identifiant": uuid.uuid4().hex
        }

        selections.append(record)

        write(
            "selections.json",
            selections
        )

        return record


# ============================================
# FIGÉ AVANT DÉPART
# ============================================

def lock(cid, verification):
    with LOCK:
        course = course_by_id(cid)

        if course is None:
            raise ValueError(
                "Course inconnue"
            )

        moment = now()
        depart = parse_date(course["depart"])

        if moment >= depart:
            raise ValueError(
                "FIGÉ après départ interdit"
            )

        if not isinstance(verification, dict):
            raise ValueError(
                "Vérification invalide"
            )

        flags = (
            "partants_confirmes",
            "np_confirmes",
            "horaire_confirme",
            "formule_confirmee",
            "source_france_confirmee"
        )

        if any(
            verification.get(key) is not True
            for key in flags
        ):
            raise ValueError(
                "Contrôles critiques non attestés"
            )

        source = verification.get("source")

        if (
            not isinstance(source, str)
            or not source.strip()
        ):
            raise ValueError(
                "Source de contrôle absente"
            )

        checked = parse_date(
            verification.get("verifie_a")
        )

        age = (
            moment - checked
        ).total_seconds()

        if not 0 <= age <= 600:
            raise ValueError(
                "Vérification future ou périmée"
            )

        source_time = (
            course.get("source_verifiee_a")
            or course.get("collecte_a")
        )

        if source_time:
            source_age = (
                moment - parse_date(source_time)
            ).total_seconds()

            if not 0 <= source_age <= 600:
                raise ValueError(
                    "Données de course périmées"
                )

        selections = [
            s for s in records("selections.json")
            if (
                s.get("course_id") == cid
                and s.get("moteur") == "M1"
            )
        ]

        if not selections:
            raise ValueError(
                "Sélection M1 absente"
            )

        selection = selections[-1]

        if selection.get("statut") != "PROVISOIRE":
            raise ValueError(
                "Sélection non provisoire"
            )

        created = parse_date(
            selection.get("cree_a")
        )

        if created > moment or created >= depart:
            raise ValueError(
                "Sélection hors délai"
            )

        ranking = numbers(
            selection.get("classement"),
            "Classement"
        )

        if (
            len(ranking) != 5
            or not set(ranking) <= starters(course)
        ):
            raise ValueError(
                "Classement invalide ou périmé"
            )

        bet = selection.get("pari_envisage")

        if bet is not None:
            validate_bet(bet, course)

        archives = records("figes.json")
        verify_archives(archives)

        previous = [
            f for f in archives
            if f["course_id"] == cid
        ]

        sid = selection.get("identifiant")

        if not isinstance(sid, str) or not sid:
            raise ValueError(
                "Identifiant sélection absent"
            )

        if any(
            f.get("selection_id") == sid
            for f in previous
        ):
            raise ValueError(
                "Sélection déjà FIGÉe"
            )

        if previous:
            last = previous[-1]

            if parse_date(last["fige_a"]) >= moment:
                raise ValueError(
                    "Nouvelle version non chronologique"
                )

            if parse_date(last["depart"]) != depart:
                raise ValueError(
                    "Départ modifié entre FIGÉs"
                )

        record = {
            "course_id": cid,
            "moteur": "M1",
            "version": len(previous) + 1,
            "depart": course["depart"],
            "fige_a": moment.isoformat(),
            "selection_id": sid,
            "selection_creee_a": selection["cree_a"],
            "classement": list(ranking),
            "pari": (
                dict(bet)
                if bet is not None
                else None
            ),
            "verification": dict(verification),
            "statut": "FIGE_AVANT_DEPART",
            "publication_distante_confirmee": False
        }

        record["sha256"] = digest(record)

        archives.append(record)

        write("figes.json", archives)

        return record


# ============================================
# AUDIT APRÈS COURSE
# ============================================

def audit(cid, arrival, source, verified=False):
    if verified is not True:
        raise ValueError(
            "Attestation officielle requise"
        )

    if (
        not isinstance(source, str)
        or not source.strip()
    ):
        raise ValueError(
            "Référence officielle absente"
        )

    numbers(arrival, "Arrivée")

    if len(arrival) < 5:
        raise ValueError(
            "Cinq positions minimum requises"
        )

    with LOCK:
        course = course_by_id(cid)

        if (
            course is None
            or now() < parse_date(course["depart"])
        ):
            raise ValueError(
                "Course inconnue ou non partie"
            )

        known = {
            p["numero"]
            for p in course["partants"]
        }

        if not set(arrival) <= known:
            raise ValueError(
                "Arrivée : cheval inconnu"
            )

        archives = records("figes.json")
        verify_archives(archives)

        previous = [
            f for f in archives
            if f["course_id"] == cid
        ]

        if not previous:
            raise ValueError(
                "Aucun FIGÉ à auditer"
            )

        fig = previous[-1]

        audits = records("audits.json")

        if any(
            a.get("course_id") == cid
            and a.get("version") == fig["version"]
            for a in audits
        ):
            raise ValueError(
                "Audit déjà présent"
            )

        predicted = fig["classement"]

        result = {
            "course_id": cid,
            "moteur": "M1",
            "version": fig["version"],
            "arrivee": list(arrival),
            "source_officielle": source.strip(),
            "resultat_verifie_declare": True,
            "recoupement_independant": False,
            "audite_a": now().isoformat(),
            "top5_detectes": len(
                set(predicted[:5])
                & set(arrival[:5])
            ),
            "ordre_exact": (
                predicted[:5] == arrival[:5]
            ),
            "rapport": None,
            "gain_theorique": None,
            "note": (
                "Résultat attesté par opérateur. "
                "Aucun recoupement automatique. "
                "Aucun gain inventé."
            )
        }

        audits.append(result)

        write("audits.json", audits)

        return result


# ============================================
# SERVEUR LOCAL LECTURE SEULE
# ============================================

class Handler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            directory=str(WEB),
            **kwargs
        )

    def do_GET(self):
        route = urlsplit(self.path).path

        mapping = {
            "/api/courses": (
                "courses.json", []
            ),
            "/api/selections": (
                "selections.json", []
            ),
            "/api/figes": (
                "figes.json", []
            ),
            "/api/audits": (
                "audits.json", []
            ),
            "/api/diagnostic": (
                "import_diagnostic.json", {}
            )
        }

        if route == "/api/status":
            return self.send_json({
                "moteur": "M1",
                "version": VERSION,
                "heure_paris": now().isoformat(),
                "mode": "LECTURE_SEULE_WEB",
                "collecte_automatique": False,
                "figes_automatiques": False
            })

        if route in mapping:
            name, fallback = mapping[route]

            try:
                with LOCK:
                    value = read(name, fallback)

                    if name == "figes.json":
                        verify_archives(value)

                return self.send_json(value)

            except (
                OSError,
                ValueError,
                TypeError,
                KeyError
            ) as exc:

                return self.send_json(
                    {"erreur": str(exc)},
                    500
                )

        return super().do_GET()

    def send_json(self, payload, status=200):
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )

        self.send_header(
            "X-Content-Type-Options",
            "nosniff"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()
        self.wfile.write(body)


# ============================================
# TESTS LOCAUX
# ============================================

def expect_error(function):
    try:
        function()
    except (
        ValueError,
        TypeError,
        KeyError
    ):
        return

    raise AssertionError(
        "Une donnée invalide a été acceptée"
    )


def self_test():
    assert parse_date(
        "2026-10-30T19:00:00+01:00"
    ).hour == 19

    for bad in (
        "2026-10-30T19:00:00",
        "non-date",
        None
    ):
        expect_error(
            lambda b=bad: parse_date(b)
        )

    expect_error(
        lambda: load_json('{"a":1,"a":2}')
    )

    expect_error(
        lambda: load_json('{"a":NaN}')
    )

    course = {
        "id": "2026-10-30-R1-C5",
        "localisation_france_verifiee": True,
        "depart": "2026-10-30T19:00:00+01:00",
        "partants": [
            {
                "numero": n,
                "nom": f"Test {n}"
            }
            for n in (9, 6, 8, 4, 7)
        ],
        "formules": [
            "Couplé gagnant",
            "Simple placé",
            "Trio"
        ]
    }

    normalized = normalize_course(course)

    assert len(normalized["partants"]) == 5

    validate_bet(
        {
            "formule": "Couplé gagnant",
            "numeros": [9, 6]
        },
        normalized
    )

    for bad in (
        {
            **course,
            "localisation_france_verifiee": False
        },
        {
            **course,
            "depart": "2026-10-31T19:00:00+01:00"
        },
        {
            **course,
            "partants": [
                {"numero": 9, "nom": "A"},
                {"numero": 9, "nom": "B"}
            ]
        },
        {
            **course,
            "partants": [
                {"numero": True, "nom": "A"}
            ]
        }
    ):
        expect_error(
            lambda b=bad: normalize_course(b)
        )

    for bad in (
        {
            "formule": "Couplé gagnant",
            "numeros": [9]
        },
        {
            "formule": "Couplé gagnant",
            "numeros": [9, 9]
        },
        {
            "formule": "Couplé gagnant",
            "numeros": [9, 10]
        },
        {
            "formule": "Trio",
            "numeros": [9, 6]
        }
    ):
        expect_error(
            lambda b=bad: validate_bet(
                b, normalized
            )
        )

    archive = {
        "course_id": normalized["id"],
        "moteur": "M1",
        "version": 1,
        "fige_a": "2026-10-30T18:30:00+01:00",
        "depart": normalized["depart"],
        "statut": "FIGE_AVANT_DEPART",
        "classement": [9, 6, 8, 4, 7]
    }

    archive["sha256"] = digest(archive)

    assert verify_archives([archive]) == 1

    expect_error(
        lambda: verify_archives([
            {**archive, "version": 2}
        ])
    )

    expect_error(
        lambda: verify_archives([
            archive, archive
        ])
    )

    print("=" * 50)
    print("🇸🇳🧠🔥 MPAA X1000 M1")
    print("VERSION :", VERSION)
    print("SELF-TEST OK")
    print("Dates : OK")
    print("Courses françaises : OK")
    print("Partants : OK")
    print("Non-partants : OK")
    print("Paris : OK")
    print("FIGÉs : OK")
    print("SHA256 : OK")
    print("Doublons : OK")
    print("JSON strict : OK")
    print("=" * 50)


# ============================================
# INTERFACE COMMANDES
# ============================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "🇸🇳🧠🔥 MPAA X1000 "
            "M1 indépendant"
        )
    )

    group = parser.add_mutually_exclusive_group()

    group.add_argument(
        "--self-test",
        action="store_true"
    )

    group.add_argument("--import-json")
    group.add_argument("--select")
    group.add_argument("--lock")
    group.add_argument("--audit")

    group.add_argument(
        "--verify-archives",
        action="store_true"
    )

    group.add_argument(
        "--serve",
        action="store_true"
    )

    parser.add_argument("--ranking")
    parser.add_argument("--analysis")
    parser.add_argument("--bet")
    parser.add_argument("--verification-json")
    parser.add_argument("--arrival")
    parser.add_argument("--source-officielle")

    parser.add_argument(
        "--resultat-verifie",
        action="store_true"
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8765
    )

    args = parser.parse_args()

    try:
        if args.self_test:
            self_test()

        elif args.verify_archives:
            count = verify_archives(
                records("figes.json")
            )

            print(
                f"Archives valides : {count} FIGÉ(s)"
            )

        elif args.import_json:
            result = import_courses(
                args.import_json
            )

            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2
            ))

        elif args.select:
            bet = (
                load_json(args.bet)
                if args.bet
                else None
            )

            result = create_selection(
                args.select,
                parse_csv(args.ranking),
                args.analysis or "",
                bet
            )

            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2
            ))

        elif args.lock:
            if not args.verification_json:
                parser.error(
                    "--lock exige --verification-json"
                )

            verification = load_json(
                Path(
                    args.verification_json
                ).read_text(encoding="utf-8")
            )

            result = lock(
                args.lock,
                verification
            )

            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2
            ))

        elif args.audit:
            result = audit(
                args.audit,
                parse_csv(args.arrival),
                args.source_officielle,
                args.resultat_verifie
            )

            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2
            ))

        elif args.serve:
            if not WEB.is_dir():
                parser.error(
                    "Dossier web/ absent"
                )

            print(
                f"M1 : http://{args.host}:{args.port}"
            )

            ThreadingHTTPServer(
                (args.host, args.port),
                Handler
            ).serve_forever()

        else:
            parser.print_help()

    except (
        ValueError,
        OSError,
        KeyError,
        TypeError
    ) as exc:

        parser.exit(
            2,
            f"ERREUR M1 : {exc}\n"
        )


if __name__ == "__main__":
    main()
