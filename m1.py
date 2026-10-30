#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🇸🇳🧠🔥 MPAA X1000 — M1 indépendant, bibliothèque standard uniquement.

Commandes :
  python m1.py --self-test
  python m1.py --import-json courses.json
  python m1.py --select 2026-10-31-R1-C1 --ranking 9,6,8,4,7 --analysis "Justification sportive documentée d'au moins trente caractères." --bet '{"formule":"Trio","numeros":[9,6,8]}'
  python m1.py --lock 2026-10-31-R1-C1 --verification-json controles.json
  python m1.py --audit 2026-10-31-R1-C1 --arrival 9,6,8,4,7 --source-officielle "Référence officielle" --resultat-verifie
  python m1.py --verify-archives
  python m1.py --serve

Aucune collecte PMU, publication distante ou validation officielle automatique.
Les attestations d'opérateur ne constituent pas un recoupement indépendant.
Les empreintes SHA-256 détectent des modifications accidentelles, sans garantir
l'authenticité face à une personne pouvant réécrire fichiers et empreintes.
"""
import argparse
import datetime as dt
import hashlib
import hmac
import json
import math
import os
import re
import tempfile
import threading
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
WEB = ROOT / 'web'
PARIS = ZoneInfo('Europe/Paris')
COURSE_ID = re.compile(r'^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$')
ALLOWED = frozenset({'courses.json', 'selections.json', 'figes.json', 'audits.json', 'import_diagnostic.json'})
LOCK = threading.RLock()
FORMULES = {
    'Simple gagnant': (1, 1), 'Simple placé': (1, 1),
    'Couplé gagnant': (2, 2), 'Couplé placé': (2, 2),
    '2sur4': (2, 2), 'Trio': (3, 3), 'Multi': (4, 7),
    'Tiercé': (3, 3), 'Quarté+': (4, 4), 'Quinté+': (5, 5),
    'Pick5': (5, 5), 'Super4': (4, 4)
}
# Bornes indicatives : ne certifient pas les conditions commerciales PMU.


def now():
    return dt.datetime.now(PARIS)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def reject_constant(value):
    raise ValueError('Constante JSON interdite : ' + value)


def reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Clé JSON dupliquée : ' + key)
        result[key] = value
    return result


def load_json(text):
    return json.loads(text, parse_constant=reject_constant,
                      object_pairs_hook=reject_duplicate_pairs)


def path_for(name):
    if name not in ALLOWED:
        raise ValueError('Fichier non autorisé')
    return DATA / name


def read(name, fallback):
    path = path_for(name)
    if not path.exists():
        return fallback
    return load_json(path.read_text(encoding='utf-8'))


def write(name, payload):
    path = path_for(name)
    DATA.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.m1-', suffix='.tmp', dir=DATA)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(DATA, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parse_date(value):
    if not isinstance(value, str):
        raise ValueError('Date ISO avec fuseau requise')
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Date ISO invalide') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Fuseau horaire absent')
    return parsed.astimezone(PARIS)


def numbers(value, label, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f'{label} : liste requise')
    if any(type(n) is not int or n <= 0 for n in value):
        raise ValueError(f'{label} : numéros entiers positifs requis')
    if len(value) != len(set(value)):
        raise ValueError(f'{label} : doublons interdits')
    return value


def records(name):
    value = read(name, [])
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise ValueError(f'{name} : liste d’objets attendue')
    return value


def normalize_course(raw):
    if not isinstance(raw, dict):
        raise ValueError('Course : objet attendu')
    cid = raw.get('id')
    if not isinstance(cid, str) or not COURSE_ID.fullmatch(cid):
        raise ValueError('Identifiant de course invalide')
    try:
        dt.date.fromisoformat(cid[:10])
    except ValueError as exc:
        raise ValueError('Date de course invalide') from exc
    if raw.get('localisation_france_verifiee') is not True:
        raise ValueError('Localisation française non vérifiée')
    depart = parse_date(raw.get('depart'))
    if depart.date().isoformat() != cid[:10]:
        raise ValueError('Date du départ incohérente')
    raw_partants = raw.get('partants')
    if not isinstance(raw_partants, list) or not raw_partants:
        raise ValueError('Partants manquants')
    cleaned, seen = [], set()
    for p in raw_partants:
        if not isinstance(p, dict):
            raise ValueError('Partant : objet attendu')
        n = p.get('numero')
        if type(n) is not int or n <= 0 or n in seen:
            raise ValueError('Numéro de cheval invalide ou dupliqué')
        seen.add(n)
        name = p.get('nom')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Nom de cheval manquant')
        np = p.get('non_partant', False)
        if type(np) is not bool:
            raise ValueError('non_partant doit être booléen')
        cleaned.append({
            'numero': n, 'nom': name.strip(), 'non_partant': np,
            'musique': p.get('musique'), 'jockey': p.get('jockey') or p.get('driver'),
            'entraineur': p.get('entraineur'), 'ferrure': p.get('ferrure'),
            'observations': p.get('observations')
        })
    forms = raw.get('formules', [])
    if not isinstance(forms, list) or any(not isinstance(f, str) for f in forms):
        raise ValueError('Formules : liste de textes attendue')
    if len(forms) != len(set(forms)) or any(f not in FORMULES for f in forms):
        raise ValueError('Formules PMU inconnues ou dupliquées')
    checked_at = raw.get('source_verifiee_a')
    if checked_at is not None:
        parse_date(checked_at)
    return {
        'id': cid, 'localisation_france_verifiee': True,
        'hippodrome': raw.get('hippodrome'), 'nom': raw.get('nom'),
        'discipline': raw.get('discipline'), 'depart': depart.isoformat(),
        'source': raw.get('source'), 'source_verifiee_a': checked_at,
        'formules': forms, 'partants': cleaned
    }


def starters(course):
    return {p['numero'] for p in course['partants'] if not p['non_partant']}


def validate_bet(bet, course):
    if not isinstance(bet, dict):
        raise ValueError('Pari : objet JSON attendu')
    if set(bet) - {'formule', 'numeros', 'mise', 'note'}:
        raise ValueError('Pari : champs inconnus')
    formula = bet.get('formule')
    if not isinstance(formula, str) or formula not in FORMULES or formula not in course['formules']:
        raise ValueError('Formule non attestée comme proposée')
    nums = numbers(bet.get('numeros'), 'Pari')
    low, high = FORMULES[formula]
    if not low <= len(nums) <= high:
        raise ValueError(f'{formula} : entre {low} et {high} numéros requis')
    if not set(nums) <= starters(course):
        raise ValueError('Pari : cheval absent ou non-partant')
    if 'mise' in bet:
        mise = bet['mise']
        if type(mise) not in (int, float) or not math.isfinite(mise) or not 0 < mise < 100000:
            raise ValueError('Mise invalide')
    if 'note' in bet and not isinstance(bet['note'], str):
        raise ValueError('Note de pari invalide')
    return bet


def verify_archives(archives):
    if not isinstance(archives, list):
        raise ValueError('FIGÉs : liste attendue')
    versions, last_times, seen_selections = {}, {}, set()
    for index, record in enumerate(archives):
        if not isinstance(record, dict):
            raise ValueError(f'FIGÉ {index} : objet attendu')
        given = record.get('sha256')
        body = {k: v for k, v in record.items() if k != 'sha256'}
        if not isinstance(given, str) or not hmac.compare_digest(given, digest(body)):
            raise ValueError(f'FIGÉ {index} : empreinte SHA-256 invalide')
        cid, version = record.get('course_id'), record.get('version')
        if not isinstance(cid, str) or not COURSE_ID.fullmatch(cid):
            raise ValueError('FIGÉ : identifiant invalide')
        if type(version) is not int or version != versions.get(cid, 0) + 1:
            raise ValueError('FIGÉ : séquence des versions invalide')
        if record.get('moteur') != 'M1' or record.get('statut') != 'FIGE_AVANT_DEPART':
            raise ValueError('FIGÉ : moteur ou statut invalide')
        moment, depart = parse_date(record.get('fige_a')), parse_date(record.get('depart'))
        if moment >= depart or depart.date().isoformat() != cid[:10]:
            raise ValueError('FIGÉ : chronologie invalide')
        if cid in last_times and last_times[cid] >= moment:
            raise ValueError('FIGÉ : versions non chronologiques')
        ranking = numbers(record.get('classement'), 'Classement FIGÉ')
        if len(ranking) != 5:
            raise ValueError('FIGÉ : cinq positions requises')
        if 'pari' in record and record['pari'] is not None and not isinstance(record['pari'], dict):
            raise ValueError('FIGÉ : pari invalide')
        sid = record.get('selection_id')
        if sid is not None:
            if not isinstance(sid, str) or not sid or (cid, sid) in seen_selections:
                raise ValueError('FIGÉ : sélection dupliquée ou invalide')
            seen_selections.add((cid, sid))
        if record.get('selection_creee_a') is not None:
            if parse_date(record['selection_creee_a']) > moment:
                raise ValueError('FIGÉ : sélection créée après FIGÉ')
        versions[cid], last_times[cid] = version, moment
    return len(archives)


def import_courses(filename):
    payload = load_json(Path(filename).read_text(encoding='utf-8'))
    source = payload if isinstance(payload, list) else payload.get('courses') if isinstance(payload, dict) else None
    if not isinstance(source, list):
        raise ValueError('Format attendu : liste ou objet courses[]')
    valid, rejected, encountered = {}, [], set()
    for raw in source:
        try:
            course = normalize_course(raw)
            cid = course['id']
            if cid in encountered:
                valid.pop(cid, None)
                raise ValueError('Course dupliquée dans le même import')
            encountered.add(cid)
            valid[cid] = course
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({'id': raw.get('id') if isinstance(raw, dict) else None, 'erreur': str(exc)})
    with LOCK:
        archives = records('figes.json')
        verify_archives(archives)
        locked = {f['course_id'] for f in archives}
        existing_list = records('courses.json')
        existing = {c['id']: c for c in existing_list}
        if len(existing) != len(existing_list):
            raise ValueError('Courses existantes : identifiants dupliqués')
        imported = 0
        for cid, course in valid.items():
            if cid in locked and existing.get(cid) != course:
                rejected.append({'id': cid, 'erreur': 'Course FIGÉE : modification interdite'})
                continue
            existing[cid] = course
            imported += 1
        write('courses.json', sorted(existing.values(), key=lambda c: c['depart']))
        report = {'date': now().isoformat(), 'importees': imported, 'rejetees': rejected}
        write('import_diagnostic.json', report)
        return report


def course_by_id(cid):
    matches = [c for c in records('courses.json') if c.get('id') == cid]
    if len(matches) > 1:
        raise ValueError('Identifiant de course dupliqué en base')
    return matches[0] if matches else None


def create_selection(cid, ranking, analysis, bet=None):
    with LOCK:
        course = course_by_id(cid)
        if course is None:
            raise ValueError('Course inconnue')
        moment = now()
        if moment >= parse_date(course['depart']):
            raise ValueError('Présélection après départ interdite')
        numbers(ranking, 'Hiérarchie')
        if len(ranking) != 5:
            raise ValueError('M1 exige exactement cinq positions prédites')
        if not set(ranking) <= starters(course):
            raise ValueError('Hiérarchie : cheval absent ou non-partant')
        if not isinstance(analysis, str) or len(analysis.strip()) < 30:
            raise ValueError('Justification sportive : au moins 30 caractères')
        if bet is not None:
            validate_bet(bet, course)
        selections = records('selections.json')
        record = {
            'course_id': cid, 'moteur': 'M1', 'classement': list(ranking),
            'analyse': analysis.strip(), 'pari_envisage': bet,
            'statut': 'PROVISOIRE', 'cree_a': moment.isoformat(),
            'identifiant': uuid.uuid4().hex
        }
        selections.append(record)
        write('selections.json', selections)
        return record


def lock(cid, verification):
    with LOCK:
        course = course_by_id(cid)
        if course is None:
            raise ValueError('Course inconnue')
        moment, depart = now(), parse_date(course['depart'])
        if moment >= depart:
            raise ValueError('FIGÉ après départ interdit')
        if not isinstance(verification, dict):
            raise ValueError('Vérification : objet JSON attendu')
        flags = ('partants_confirmes', 'np_confirmes', 'horaire_confirme',
                 'formule_confirmee', 'source_france_confirmee')
        if any(verification.get(key) is not True for key in flags):
            raise ValueError('Contrôles critiques non attestés')
        if not isinstance(verification.get('source'), str) or not verification['source'].strip():
            raise ValueError('Source de contrôle manquante')
        checked = parse_date(verification.get('verifie_a'))
        age = (moment - checked).total_seconds()
        if not 0 <= age <= 600:
            raise ValueError('Vérification future ou périmée (> 10 min)')
        if course.get('source_verifiee_a'):
            source_age = (moment - parse_date(course['source_verifiee_a'])).total_seconds()
            if not 0 <= source_age <= 600:
                raise ValueError('Données de course trop anciennes pour FIGÉ')
        selections = [s for s in records('selections.json') if s.get('course_id') == cid]
        if not selections:
            raise ValueError('Présélection M1 absente')
        selection = selections[-1]
        if selection.get('moteur') != 'M1' or selection.get('statut') != 'PROVISOIRE':
            raise ValueError('Présélection invalide')
        created = parse_date(selection.get('cree_a'))
        if created > moment or created >= depart:
            raise ValueError('Présélection non antérieure au départ')
        ranking = numbers(selection.get('classement'), 'Hiérarchie')
        if len(ranking) != 5 or not set(ranking) <= starters(course):
            raise ValueError('Hiérarchie périmée, incomplète ou non-partant')
        bet = selection.get('pari_envisage')
        if bet is not None:
            validate_bet(bet, course)
        archives = records('figes.json')
        verify_archives(archives)
        previous = [f for f in archives if f['course_id'] == cid]
        selection_id = selection.get('identifiant')
        if not isinstance(selection_id, str) or not selection_id:
            raise ValueError('Identifiant de sélection absent')
        if any(f.get('selection_id') == selection_id for f in previous):
            raise ValueError('Présélection déjà FIGÉE')
        if previous and parse_date(previous[-1]['fige_a']) >= moment:
            raise ValueError('Versions FIGÉ non chronologiques')
        if previous and parse_date(previous[-1]['depart']) != depart:
            raise ValueError('Départ modifié entre deux versions FIGÉ')
        record = {
            'course_id': cid, 'moteur': 'M1', 'version': len(previous) + 1,
            'depart': course['depart'], 'fige_a': moment.isoformat(),
            'selection_id': selection_id, 'selection_creee_a': selection['cree_a'],
            'classement': list(ranking), 'pari': dict(bet) if bet is not None else None,
            'verification': dict(verification), 'statut': 'FIGE_AVANT_DEPART',
            'publication_distante_confirmee': False
        }
        record['sha256'] = digest(record)
        archives.append(record)
        write('figes.json', archives)
        return record


def audit(cid, arrival, source, verified=False):
    if verified is not True:
        raise ValueError('Attestation de résultat officiel requise')
    if not isinstance(source, str) or not source.strip():
        raise ValueError('Référence officielle manquante')
    numbers(arrival, 'Arrivée')
    if len(arrival) < 5:
        raise ValueError('Arrivée : cinq positions au minimum requises')
    with LOCK:
        course = course_by_id(cid)
        if course is None or now() < parse_date(course['depart']):
            raise ValueError('Course inconnue ou non partie')
        if not set(arrival) <= starters(course):
            raise ValueError('Arrivée : numéro inconnu ou non-partant')
        archives = records('figes.json')
        verify_archives(archives)
        previous = [f for f in archives if f['course_id'] == cid]
        if not previous:
            raise ValueError('Aucun FIGÉ à auditer')
        fig = previous[-1]
        audits = records('audits.json')
        if any(a.get('course_id') == cid and a.get('version') == fig['version'] for a in audits):
            raise ValueError('Audit déjà présent pour cette version')
        predicted = fig['classement']
        result = {
            'course_id': cid, 'version': fig['version'], 'arrivee': list(arrival),
            'source_officielle': source.strip(), 'resultat_verifie_declare': True,
            'recoupement_independant': False,
            'audite_a': now().isoformat(),
            'top5_detectes': len(set(predicted[:5]) & set(arrival[:5])),
            'ordre_exact': predicted[:5] == arrival[:5],
            'rapport': None, 'gain_theorique': None,
            'note': 'Source attestée par opérateur, non recoupée automatiquement. Aucun gain inventé.'
        }
        audits.append(result)
        write('audits.json', audits)
        return result


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def do_GET(self):
        route = urlsplit(self.path).path
        mapping = {
            '/api/courses': ('courses.json', []),
            '/api/selections': ('selections.json', []),
            '/api/figes': ('figes.json', []),
            '/api/audits': ('audits.json', []),
            '/api/diagnostic': ('import_diagnostic.json', {})
        }
        if route == '/api/status':
            return self.send_json({
                'moteur': 'M1', 'heure_paris': now().isoformat(),
                'mode': 'LECTURE_SEULE_WEB', 'collecte_automatique': False,
                'figes_automatiques': False
            })
        if route in mapping:
            name, fallback = mapping[route]
            try:
                with LOCK:
                    value = read(name, fallback)
                    if name == 'figes.json':
                        verify_archives(value)
                return self.send_json(value)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                return self.send_json({'erreur': str(exc)}, 500)
        return super().do_GET()

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def expect_error(function):
    try:
        function()
    except (ValueError, TypeError, KeyError):
        return
    raise AssertionError('Une donnée invalide a été acceptée')


def self_test():
    assert parse_date('2026-10-30T19:00:00+01:00').hour == 19
    for bad in ('2026-10-30T19:00:00', 'non-date', None):
        expect_error(lambda b=bad: parse_date(b))
    expect_error(lambda: load_json('{"a":1,"a":2}'))
    expect_error(lambda: load_json('{"a":NaN}'))
    course = {
        'id': '2026-10-30-R1-C5', 'localisation_france_verifiee': True,
        'depart': '2026-10-30T19:00:00+01:00',
        'partants': [{'numero': n, 'nom': f'Test {n}'} for n in (9, 6, 8, 4, 7)],
        'formules': ['Couplé gagnant', 'Simple placé', 'Trio']
    }
    normalized = normalize_course(course)
    assert len(normalized['partants']) == 5
    validate_bet({'formule': 'Couplé gagnant', 'numeros': [9, 6]}, normalized)
    for bad in (
        {**course, 'localisation_france_verifiee': False},
        {**course, 'depart': '2026-10-31T19:00:00+01:00'},
        {**course, 'partants': [{'numero': 9, 'nom': 'A'}, {'numero': 9, 'nom': 'B'}]},
        {**course, 'partants': [{'numero': True, 'nom': 'A'}]}
    ):
        expect_error(lambda b=bad: normalize_course(b))
    for bad in (
        {'formule': 'Couplé gagnant', 'numeros': [9]},
        {'formule': 'Couplé gagnant', 'numeros': [9, 9]},
        {'formule': 'Couplé gagnant', 'numeros': [9, 10]},
        {'formule': 'Trio', 'numeros': [9, 6]},
        {'formule': 'Couplé gagnant', 'numeros': [9, 6], 'mise': float('nan')}
    ):
        expect_error(lambda b=bad: validate_bet(b, normalized))
    archive = {
        'course_id': normalized['id'], 'moteur': 'M1', 'version': 1,
        'fige_a': '2026-10-30T18:30:00+01:00', 'depart': normalized['depart'],
        'statut': 'FIGE_AVANT_DEPART', 'classement': [9, 6, 8, 4, 7]
    }
    archive['sha256'] = digest(archive)
    assert verify_archives([archive]) == 1
    expect_error(lambda: verify_archives([{**archive, 'version': 2}]))
    expect_error(lambda: verify_archives([archive, archive]))
    print('SELF-TEST OK : dates, France, partants, paris, FIGÉs, doublons, NaN')


def parse_csv(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Liste de numéros absente')
    try:
        return numbers([int(part.strip()) for part in value.split(',')], 'Numéros')
    except ValueError as exc:
        raise ValueError('Liste de numéros invalide : ' + str(exc)) from exc


def main():
    parser = argparse.ArgumentParser(description='MPAA X1000 — M1 indépendant')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--self-test', action='store_true')
    group.add_argument('--import-json')
    group.add_argument('--select')
    group.add_argument('--lock')
    group.add_argument('--audit')
    group.add_argument('--verify-archives', action='store_true')
    group.add_argument('--serve', action='store_true')
    parser.add_argument('--ranking')
    parser.add_argument('--analysis')
    parser.add_argument('--bet')
    parser.add_argument('--verification-json')
    parser.add_argument('--arrival')
    parser.add_argument('--source-officielle')
    parser.add_argument('--resultat-verifie', action='store_true')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        elif args.verify_archives:
            print(f'Archives valides : {verify_archives(records("figes.json"))} FIGÉ(s)')
        elif args.import_json:
            print(json.dumps(import_courses(args.import_json), ensure_ascii=False, indent=2))
        elif args.select:
            bet = load_json(args.bet) if args.bet else None
            result = create_selection(args.select, parse_csv(args.ranking), args.analysis or '', bet)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.lock:
            if not args.verification_json:
                parser.error('--lock exige --verification-json')
            verification = load_json(Path(args.verification_json).read_text(encoding='utf-8'))
            print(json.dumps(lock(args.lock, verification), ensure_ascii=False, indent=2))
        elif args.audit:
            result = audit(args.audit, parse_csv(args.arrival), args.source_officielle, args.resultat_verifie)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.serve:
            if not WEB.is_dir():
                parser.error('Dossier web/ absent : ajoutez web/index.html')
            print(f'M1 : http://{args.host}:{args.port} — Ctrl+C pour arrêter')
            ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
        else:
            parser.print_help()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'ERREUR M1 : {exc}\n')


if __name__ == '__main__':
    main()
