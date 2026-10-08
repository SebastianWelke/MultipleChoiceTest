#!/usr/bin/env python3
"""Lokaler Webserver für das Quiz.

Start:  python3 server.py [--port 8000] [--no-browser]
Dann im Browser: http://localhost:8000/quiz.html

API:
  GET    /api/settings            -> aktuelle Einstellungen
  PUT    /api/settings            -> Einstellungen speichern (JSON-Body)
  GET    /api/catalogs            -> Liste aller Fragenkataloge
  GET    /api/catalogs/<id>       -> Fragen eines Katalogs
  POST   /api/catalogs            -> Katalog hochladen ({"name": ..., "fragen": [...]})
  DELETE /api/catalogs/<id>       -> hochgeladenen Katalog löschen
"""

import argparse
import json
import os
import re
import threading
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_DIR = os.path.join(BASE_DIR, "kataloge")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

DEFAULT_CATALOG = "tisp"
# Mitgelieferte Kataloge lassen sich nicht über die Oberfläche löschen.
PROTECTED_IDS = {"tisp"}

DEFAULT_SETTINGS = {
    "showAnswerCount": True,
    "activeCatalog": DEFAULT_CATALOG,
}

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Nur diese Dateien werden statisch ausgeliefert. Alles andere im
# Projektverzeichnis -- .git, Footage, settings.json -- bleibt unerreichbar.
STATIC_FILES = {"/quiz.html", "/settings.html", "/style.css"}

# Gegen DNS-Rebinding und CSRF; wird in main() aus dem Port gefuellt.
ALLOWED_HOSTS = set()
ALLOWED_ORIGINS = set()

_lock = threading.Lock()


def configure_allowed_origins(port):
    """Legt fest, welche Host- und Origin-Header als lokal gelten."""
    hosts = {"localhost:%d" % port, "127.0.0.1:%d" % port, "[::1]:%d" % port}
    if port == 80:
        hosts |= {"localhost", "127.0.0.1", "[::1]"}
    ALLOWED_HOSTS.clear()
    ALLOWED_HOSTS.update(hosts)
    ALLOWED_ORIGINS.clear()
    ALLOWED_ORIGINS.update("http://" + h for h in hosts)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------- Datenhaltung ----------

def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            stored = read_json(SETTINGS_FILE)
            if isinstance(stored, dict):
                settings.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
        except (OSError, ValueError):
            pass
    if not catalog_exists(settings["activeCatalog"]):
        vorhanden = [c["id"] for c in list_catalogs()]
        settings["activeCatalog"] = (DEFAULT_CATALOG if DEFAULT_CATALOG in vorhanden
                                     else (vorhanden[0] if vorhanden else DEFAULT_CATALOG))
    return settings


def save_settings(new_values):
    if not isinstance(new_values, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Einstellungen müssen ein JSON-Objekt sein.")
    settings = load_settings()
    if "showAnswerCount" in new_values:
        if not isinstance(new_values["showAnswerCount"], bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, "showAnswerCount muss true oder false sein.")
        settings["showAnswerCount"] = new_values["showAnswerCount"]
    if "activeCatalog" in new_values:
        if not catalog_exists(new_values["activeCatalog"]):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Unbekannter Fragenkatalog.")
        settings["activeCatalog"] = new_values["activeCatalog"]
    write_json(SETTINGS_FILE, settings)
    return settings


def catalog_path(catalog_id):
    if not isinstance(catalog_id, str) or not ID_PATTERN.match(catalog_id):
        return None
    return os.path.join(CATALOG_DIR, catalog_id + ".json")


def catalog_exists(catalog_id):
    path = catalog_path(catalog_id)
    return path is not None and os.path.exists(path)


def load_catalog(catalog_id):
    path = catalog_path(catalog_id)
    if path is None or not os.path.exists(path):
        raise ApiError(HTTPStatus.NOT_FOUND, "Fragenkatalog nicht gefunden.")
    data = read_json(path)
    # Üblich ist {"name": ..., "fragen": [...]}; eine nackte Liste von Fragen
    # wird ebenfalls gelesen, dann dient die Dateiname als Katalogname.
    if isinstance(data, list):
        name, fragen = catalog_id, data
    else:
        name, fragen = data.get("name", catalog_id), data.get("fragen", [])
    return {"id": catalog_id, "name": name, "builtin": catalog_id in PROTECTED_IDS,
            "fragen": fragen}


def list_catalogs():
    ids = []
    if os.path.isdir(CATALOG_DIR):
        ids = sorted(f[:-5] for f in os.listdir(CATALOG_DIR)
                     if f.endswith(".json") and ID_PATTERN.match(f[:-5]))
    result = []
    for cid in ids:
        try:
            cat = load_catalog(cid)
        except (OSError, ValueError, ApiError):
            continue
        result.append({
            "id": cat["id"],
            "name": cat["name"],
            "builtin": cat["builtin"],
            "anzahl": len(cat["fragen"]),
            "module": sorted({q.get("modul", "") for q in cat["fragen"]}),
        })
    return result


def slugify(name):
    s = name.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:48] or "katalog"


def validate_questions(fragen, default_modul):
    """Prüft das Fragenformat und gibt eine bereinigte Liste zurück."""
    if not isinstance(fragen, list) or not fragen:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Die Datei muss eine nicht-leere Liste von Fragen enthalten.")
    cleaned = []
    for i, q in enumerate(fragen, start=1):
        where = "Frage %d: " % i
        if not isinstance(q, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, where + "muss ein Objekt sein.")
        frage = q.get("frage")
        if not isinstance(frage, str) or not frage.strip():
            raise ApiError(HTTPStatus.BAD_REQUEST, where + "Feld 'frage' fehlt oder ist leer.")
        optionen = q.get("optionen")
        if (not isinstance(optionen, dict) or len(optionen) < 2
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in optionen.items())):
            raise ApiError(HTTPStatus.BAD_REQUEST,
                           where + "'optionen' muss ein Objekt mit mindestens 2 Texten sein, z. B. {\"A\": \"...\", \"B\": \"...\"}.")
        if len(optionen) > 26:
            raise ApiError(HTTPStatus.BAD_REQUEST, where + "maximal 26 Antwortoptionen erlaubt.")
        richtig = q.get("richtig")
        if isinstance(richtig, str):
            richtig = [richtig]
        # Eine leere Liste ist erlaubt: dann ist keine der Antworten richtig.
        if not isinstance(richtig, list):
            raise ApiError(HTTPStatus.BAD_REQUEST, where + "'richtig' muss eine Liste sein, z. B. [\"A\", \"C\"] -- oder [] , wenn keine Antwort richtig ist.")
        unknown = [r for r in richtig if r not in optionen]
        if unknown:
            raise ApiError(HTTPStatus.BAD_REQUEST,
                           where + "'richtig' enthält unbekannte Option(en): %s." % ", ".join(map(str, unknown)))
        modul = q.get("modul")
        if not isinstance(modul, str) or not modul.strip():
            modul = default_modul
        entry = {"frage": frage, "optionen": optionen,
                 "richtig": list(dict.fromkeys(richtig)), "modul": modul}

        # Optionale Zusatzangaben werden übernommen, alles andere verworfen.
        for key in ("nummer", "lerninhalt"):
            if isinstance(q.get(key), str) and q[key].strip():
                entry[key] = q[key].strip()[:200]
        level = q.get("level")
        if isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= 3:
            entry["level"] = level
        elif level is not None:
            raise ApiError(HTTPStatus.BAD_REQUEST, where + "'level' muss 1, 2 oder 3 sein.")
        quelle = q.get("quelle")
        if isinstance(quelle, str) and quelle.strip():
            if not quelle.strip().startswith(("http://", "https://")):
                raise ApiError(HTTPStatus.BAD_REQUEST, where + "'quelle' muss mit http:// oder https:// beginnen.")
            entry["quelle"] = quelle.strip()

        cleaned.append(entry)
    return cleaned


def create_catalog(payload):
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Ungültige Anfrage.")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ApiError(HTTPStatus.BAD_REQUEST, "Bitte einen Namen für den Fragenkatalog angeben.")
    name = name.strip()[:80]
    fragen = validate_questions(payload.get("fragen"), default_modul=name.replace(" ", "_"))

    base = slugify(name)
    os.makedirs(CATALOG_DIR, exist_ok=True)
    catalog_id, n = base, 2
    while catalog_exists(catalog_id):
        catalog_id = "%s-%d" % (base, n)
        n += 1
    write_json(os.path.join(CATALOG_DIR, catalog_id + ".json"), {"name": name, "fragen": fragen})
    return {"id": catalog_id, "name": name, "anzahl": len(fragen)}


def delete_catalog(catalog_id):
    if catalog_id in PROTECTED_IDS:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Der mitgelieferte Katalog kann nicht gelöscht werden.")
    if not catalog_exists(catalog_id):
        raise ApiError(HTTPStatus.NOT_FOUND, "Fragenkatalog nicht gefunden.")
    os.remove(catalog_path(catalog_id))
    settings = load_settings()  # setzt activeCatalog bei Bedarf auf den Standard zurück
    write_json(SETTINGS_FILE, settings)


# ---------- HTTP ----------

class QuizHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def end_headers(self):
        # Immer frische Dateien ausliefern, damit Änderungen sofort sichtbar sind.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _static_path(self):
        """Der angefragte Pfad, falls er auf der Allowlist steht, sonst None."""
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        return path if path in STATIC_FILES else None

    def _check_host(self):
        """Gegen DNS-Rebinding: nur die lokalen Namen duerfen uns ansprechen."""
        if (self.headers.get("Host") or "").lower() in ALLOWED_HOSTS:
            return True
        self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Ungueltiger Host-Header."})
        return False

    def _check_origin(self):
        """Gegen CSRF: eine fremde Seite darf die API nicht ansprechen.

        Ein fehlender Origin ist erlaubt -- Browser senden ihn bei direkter
        Navigation nicht, und Werkzeuge wie curl kennen ihn gar nicht.
        """
        origin = self.headers.get("Origin")
        if origin is None or origin.lower() in ALLOWED_ORIGINS:
            return True
        self._send_json(HTTPStatus.FORBIDDEN, {"error": "Anfrage von fremder Herkunft abgelehnt."})
        return False

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._api("GET")
            return
        if not self._check_host():
            return
        if self.path in ("/", "/index.html"):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/quiz.html")
            self.end_headers()
            return
        if self._static_path() is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Nicht gefunden."})
            return
        super().do_GET()

    def do_HEAD(self):
        if not self._check_host():
            return
        if self._static_path() is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Nicht gefunden."})
            return
        super().do_HEAD()

    def do_POST(self):
        self._api("POST")

    def do_PUT(self):
        self._api("PUT")

    def do_DELETE(self):
        self._api("DELETE")

    def _read_body(self):
        # Ohne diese Pruefung waere ein Upload per text/plain ein CORS-"Simple
        # Request": ohne Preflight und damit von jeder fremden Seite ausloesbar.
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                           "Content-Type muss application/json sein.")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Datei ist zu groß (max. 20 MB).")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Ungültiges JSON.")

    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _api(self, method):
        if not self._check_host() or not self._check_origin():
            return
        path = self.path.split("?", 1)[0].rstrip("/")
        try:
            with _lock:
                if path == "/api/settings" and method == "GET":
                    return self._send_json(HTTPStatus.OK, load_settings())
                if path == "/api/settings" and method == "PUT":
                    return self._send_json(HTTPStatus.OK, save_settings(self._read_body()))
                if path == "/api/catalogs" and method == "GET":
                    return self._send_json(HTTPStatus.OK, list_catalogs())
                if path == "/api/catalogs" and method == "POST":
                    return self._send_json(HTTPStatus.CREATED, create_catalog(self._read_body()))
                if path.startswith("/api/catalogs/"):
                    catalog_id = path[len("/api/catalogs/"):]
                    if method == "GET":
                        return self._send_json(HTTPStatus.OK, load_catalog(catalog_id))
                    if method == "DELETE":
                        delete_catalog(catalog_id)
                        return self._send_json(HTTPStatus.OK, {"ok": True})
            raise ApiError(HTTPStatus.NOT_FOUND, "Unbekannter API-Endpunkt.")
        except ApiError as e:
            self._send_json(e.status, {"error": e.message})
        except (OSError, ValueError) as e:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Serverfehler: %s" % e})


def main():
    parser = argparse.ArgumentParser(description="Startet den Quiz-Webserver.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch öffnen")
    args = parser.parse_args()

    configure_allowed_origins(args.port)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), QuizHandler)
    url = "http://localhost:%d/quiz.html" % args.port
    print("Quiz läuft unter %s  (Beenden mit Strg+C)" % url)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
