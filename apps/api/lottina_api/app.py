from flask import Flask, render_template, abort, request
from jinja2 import TemplateNotFound
from datetime import datetime

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

# ---------------------------------------------------------------------------
# Globals für alle Templates
# ---------------------------------------------------------------------------
@app.context_processor
def inject_globals():
    return {
        "current_year": datetime.now().year,
        "request_path": request.path,
        "current_user": None,  # später durch echte Auth ersetzen
    }

# ---------------------------------------------------------------------------
# Healthcheck (für Sliplane)
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True}, 200

# ---------------------------------------------------------------------------
# Kernseiten
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return render_template("index.html")

@app.get("/results")
def results():
    return render_template("results.html")

@app.get("/event/<slug_or_id>")
def event_detail(slug_or_id):
    return render_template("event.html", event_id=slug_or_id)

# ---------------------------------------------------------------------------
# Account / Auth
# ---------------------------------------------------------------------------
@app.get("/login")
def login():
    return render_template("login.html")

@app.get("/register")
def register():
    return render_template("register.html")

@app.get("/profil")
def profil():
    return render_template("profil.html")

# ---------------------------------------------------------------------------
# Anbieter / Events erstellen
# ---------------------------------------------------------------------------
@app.get("/event-erstellen")
def event_erstellen():
    return render_template("event-erstellen.html")

# ---------------------------------------------------------------------------
# Statische Infoseiten
# ---------------------------------------------------------------------------
STATIC_PAGES = {
    "impressum": "impressum.html",
    "datenschutz": "datenschutz.html",
    "nutzungsbedingungen": "nutzungsbedingungen.html",
    "preise": "preise.html",
    "so_funktionierts": "so_funktionierts.html",
    "ueber_uns": "ueber_uns.html",
    "cookie": "cookie.html",
    "vorgaben": "vorgaben.html",
}

for endpoint, tpl in STATIC_PAGES.items():
    route = f"/{endpoint.replace('_', '-')}"
    def make_view(template_name):
        def _view():
            try:
                return render_template(template_name)
            except TemplateNotFound:
                abort(404)
        return _view
    app.add_url_rule(
        rule=route,
        endpoint=endpoint,  # wichtig für url_for('preise')
        view_func=make_view(tpl)
    )

# ---------------------------------------------------------------------------
# Fehlerseiten
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    try:
        return render_template("404.html"), 404
    except TemplateNotFound:
        return "Seite nicht gefunden", 404

@app.errorhandler(500)
def server_error(e):
    try:
        return render_template("500.html"), 500
    except TemplateNotFound:
        return "Interner Serverfehler", 500

# ---------------------------------------------------------------------------
# Lokaler Dev-Start
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
