#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 M1 — Préanalyse sportive autonome, lecture seule (v1.1).

Entrée : data/collecte_m1_pmu.json
Sortie : JSON hors du dépôt, via --output.
Aucun pronostic officiel, pari, sélection M1 ou FIGÉ créé.
"""
import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PARIS = ZoneInfo("Europe/Paris")
SOURCE = ROOT / "data" / "collecte_m1_pmu.json"
VERSION = "M1-ANALYSE-1.1"
FRAICHEUR_MINUTES = 30
MARGE_DEPART_MINUTES = 5
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$")
# Trot attelé/monté et plat/obstacle : la discipline est contrôlée par course.
# Ne pas confondre année (24), rang 2, et type de course :
# seules les musiques composées intégralement de jetons reconnus sont exploitées.
TOKEN_RE = re.compile(r"(\d{1,2}|D|A|R)([amtsh])", re.I)


def now():
    return dt.datetime.now(PARIS)


def parse_date(value):
    if not isinstance(value, str):
        raise ValueError("Date ISO absente")
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Date ISO invalide") from exc
    if d.tzinfo is None or d.utcoffset() is None:
        raise ValueError("Fuseau horaire absent")
    return d.astimezone(PARIS)


def strict_json(path):
    def pairs(items):
        obj = {}
        for k, v in items:
            if k in obj:
                raise ValueError("Clé JSON dupliquée : " + k)
            obj[k] = v
        return obj

    def reject(value):
        raise ValueError("Constante JSON interdite : " + value)

    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      object_pairs_hook=pairs, parse_constant=reject)


def safe_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        n = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return n if math.isfinite(n) and n >= 0 else None


def performances(musique, discipline=None):
    """Retourne [] si une notation est ambiguë ou non comparable.

    Exemples acceptés : 1a2a3a, (24)1a2a(23)3a.
    Les marques D/A/R et 0 sont comptées comme contre-performances.
    """
    if not isinstance(musique, str) or len(musique) > 250:
        return []
    # Les années entre parenthèses sont des séparateurs, pas des rangs.
    compact = re.sub(r"\((?:19|20)?\d{2}\)", "", musique)
    compact = re.sub(r"[\s/.,;|\-]", "", compact)
    if not compact:
        return []
    matches = list(TOKEN_RE.finditer(compact))
    if not matches or "".join(m.group(0) for m in matches).lower() != compact.lower():
        return []
    allowed = None
    if isinstance(discipline, str):
        d = discipline.lower()
        if "attel" in d or "trot" in d and "mont" not in d:
            allowed = {"a"}
        elif "mont" in d:
            allowed = {"m"}
        elif "plat" in d:
            allowed = {"p"}
        elif "haie" in d:
            allowed = {"h"}
        elif "steeple" in d:
            allowed = {"s"}
    if allowed is None:
        # Sans discipline fiable, ne pas agréger des résultats hétérogènes.
        kinds = {m.group(2).lower() for m in matches}
        if len(kinds) != 1:
            return []
        allowed = kinds
    result = []
    for m in matches[:8]:
        if m.group(2).lower() not in allowed:
            continue
        rank = m.group(1).upper()
        position = int(rank) if rank.isdigit() else None
        points = max(0.0, 11.0 - position) if position and position > 0 else 0.0
        result.append({"position": rank, "points": points})
    return result


def analyse_cheval(p, discipline=None):
    if not isinstance(p, dict):
        raise ValueError("Partant invalide")
    n, nom = p.get("numero"), p.get("nom")
    if type(n) is not int or n <= 0 or not isinstance(nom, str) or not nom.strip():
        raise ValueError("Numéro ou nom invalide")
    hist = performances(p.get("musique"), discipline)
    if len(hist) < 3:
        return None
    weights = [1.0 / (1 + 0.3 * i) for i in range(len(hist))]
    forme = sum(h["points"] * w for h, w in zip(hist, weights)) / sum(weights)
    podiums = sum(h["position"].isdigit() and
                  1 <= int(h["position"]) <= 3 for h in hist)
    regularite = podiums / len(hist)
    return {
        "numero": n, "nom": nom.strip(),
        "score_heuristique": round(0.8 * forme + 2 * regularite, 4),
        "forme_recente": round(forme, 3),
        "regularite_podium": round(regularite, 3),
        "performances_lisibles": len(hist),
        "gains_declares": safe_number(p.get("gains")),
        "jockey_driver": p.get("jockey") or p.get("driver"),
        "entraineur": p.get("entraineur"),
        "musique": p.get("musique"),
        "limites": ["Distance non évaluée", "Terrain non évalué",
                    "Opposition non évaluée", "Aptitude au parcours non évaluée",
                    "Forme de l'entourage non évaluée"]
    }


def analyse_course(course, instant):
    if not isinstance(course, dict):
        return None, "Course non structurée"
    cid = course.get("id")
    if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
        return None, "Identifiant invalide"
    if course.get("localisation_france_verifiee") is not True:
        return None, "Localisation française non vérifiée"
    try:
        depart = parse_date(course.get("depart"))
    except ValueError:
        return None, "Départ invalide"
    if depart.date().isoformat() != cid[:10]:
        return None, "Date du départ incohérente"
    if depart <= instant + dt.timedelta(minutes=MARGE_DEPART_MINUTES):
        return None, "Départ passé ou trop proche"
    partants = course.get("partants")
    if not isinstance(partants, list) or not partants:
        return None, "Liste des partants absente"
    seen, eligible, np = set(), [], []
    for p in partants:
        if not isinstance(p, dict):
            return None, "Partant non structuré"
        n = p.get("numero")
        if type(n) is not int or n <= 0 or n in seen:
            return None, "Numéros absents ou dupliqués"
        seen.add(n)
        if type(p.get("non_partant")) is not bool:
            return None, "Statut non-partant non confirmé"
        if p["non_partant"]:
            np.append(n)
            continue
        try:
            a = analyse_cheval(p, course.get("discipline"))
        except ValueError as exc:
            return None, str(exc)
        if a is not None:
            eligible.append(a)
    if len(eligible) < 5:
        return None, "Moins de cinq chevaux avec historique exploitable"
    eligible.sort(key=lambda x: (-x["score_heuristique"],
                                 -x["forme_recente"], x["numero"]))
    top = eligible[:5]
    return {
        "course_id": cid, "moteur": "M1", "methode": VERSION,
        "statut": "PROPOSITION_NON_VALIDEE",
        "depart": depart.isoformat(), "hippodrome": course.get("hippodrome"),
        "classement": [x["numero"] for x in top], "details": top,
        "chevaux_evalues": len(eligible), "non_partants_exclus": np,
        "analyse": "Préclassement heuristique sur musique lisible et régularité ; "
                   "les facteurs de distance, terrain, opposition et entourage "
                   "ne sont pas évalués. Ce score n'est pas une probabilité de victoire.",
        "pari_envisage": None, "fige": False,
        "publication_officielle": False
    }, None


def produire(source, instant=None):
    instant = instant or now()
    data = strict_json(source)
    if not isinstance(data, dict) or data.get("statut") != "OK":
        raise ValueError("Collecte non validée")
    if data.get("erreurs") != []:
        raise ValueError("Erreurs de collecte ou format non vérifié")
    if data.get("date") != instant.date().isoformat():
        raise ValueError("Collecte hors date Paris")
    collecte = parse_date(data.get("collecte_a"))
    age = (instant - collecte).total_seconds()
    if not 0 <= age <= FRAICHEUR_MINUTES * 60:
        raise ValueError("Collecte trop ancienne ou future")
    courses = data.get("courses")
    if not isinstance(courses, list):
        raise ValueError("courses : liste attendue")
    counts = {}
    for c in courses:
        if isinstance(c, dict) and isinstance(c.get("id"), str):
            counts[c["id"]] = counts.get(c["id"], 0) + 1
    propositions, rejets = [], []
    for c in courses:
        cid = c.get("id") if isinstance(c, dict) else None
        if cid is not None and counts.get(cid, 0) > 1:
            rejets.append({"course_id": cid, "raison": "Identifiant dupliqué"})
            continue
        proposal, reason = analyse_course(c, instant)
        if proposal is None:
            rejets.append({"course_id": cid, "raison": reason})
        else:
            propositions.append(proposal)
    return {
        "version": VERSION, "moteur": "M1", "genere_a": instant.isoformat(),
        "source_collecte": str(source), "statut": "PREPUBLICATION",
        "propositions": propositions, "courses_ecartees": rejets,
        "nombre_propositions": len(propositions),
        "aucune_modification_figes": True,
        "validation_sportive_humaine": False,
        "avertissement": "Scores heuristiques non validés ; aucun FIGÉ, pari "
                         "ou pronostic officiel publié."
    }


def self_test():
    t = dt.datetime(2026, 10, 31, 12, 0, tzinfo=PARIS)
    partants = [{"numero": n, "nom": f"Cheval test {n}",
                 "non_partant": n == 8,
                 "musique": "1a2a3a4a5a" if n % 2 else "4a5a6a7a8a"}
                for n in range(1, 9)]
    c = {"id": "2026-10-31-R1-C1", "depart": "2026-10-31T15:00:00+01:00",
         "localisation_france_verifiee": True, "discipline": "Trot attelé",
         "partants": partants}
    p, e = analyse_course(c, t)
    assert e is None and len(p["classement"]) == 5
    assert 8 not in p["classement"] and p["fige"] is False
    assert len(performances("(24)1a2a(23)3a", "Trot attelé")) == 3
    assert performances("inconnue") == []
    assert performances("1a2m3a", "Trot attelé") and len(performances("1a2m3a", "Trot attelé")) == 2
    assert analyse_course({**c, "depart": "2026-10-31T11:00:00+01:00"}, t)[0] is None
    assert analyse_course({**c, "localisation_france_verifiee": False}, t)[0] is None
    assert analyse_course({**c, "partants": [{**x, "musique": "?"} for x in partants]}, t)[0] is None
    duplicate = [c, dict(c)]
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as d:
        path = Path(d) / "collecte.json"
        payload = {"statut": "OK", "erreurs": [], "date": "2026-10-31",
                   "collecte_a": "2026-10-31T12:00:00+01:00", "courses": duplicate}
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert produire(path, t)["nombre_propositions"] == 0
    print("M1 analyse autonome : SELF-TEST OK")


def write_report(path, report):
    output = Path(path).absolute()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("Sortie interdite dans le dépôt")
    if output.is_symlink() or output.exists() and not output.is_file():
        raise ValueError("Destination invalide")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Pas de suivi de liens symboliques sur les composants de sortie.
    parent = output.parent
    while parent != parent.parent:
        if parent.is_symlink():
            raise ValueError("Répertoire de sortie symbolique interdit")
        parent = parent.parent
    fd, temp = tempfile.mkstemp(prefix=".m1-", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, output)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(SOURCE))
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.output:
        parser.error("--output obligatoire")
    report = produire(Path(args.source))
    write_report(args.output, report)
    print("Propositions M1 :", report["nombre_propositions"])
    print("Rapport :", args.output)
    print("Fichiers M1 protégés : non modifiés")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, TypeError, OSError, KeyError) as exc:
        print("ERREUR M1 ANALYSE :", exc, file=sys.stderr)
        sys.exit(2)
