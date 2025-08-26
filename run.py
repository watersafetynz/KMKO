# run.py
import os
from typing import Optional

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)
from dotenv import load_dotenv
from sqlalchemy import text

# Load environment variables early
load_dotenv()

from db import get_engine  # noqa: E402 (import after load_dotenv)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET", "dev-secret")


# --------------------------
# DB helpers
# --------------------------
def fetch_funders():
    """Return all funders (FunderID, Description, RouteName)."""
    with get_engine().connect() as conn:
        return conn.execute(
            text("EXEC dbo.KMKO_HelperFunctions @Request=:r"),
            {"r": "ListFunders"},
        ).mappings().all()


def fetch_funder_by_route(route_name: str) -> Optional[dict]:
    """Return a single funder by RouteName, or None if not found."""
    with get_engine().connect() as conn:
        return conn.execute(
            text("EXEC dbo.KMKO_HelperFunctions @Request=:r, @RouteName=:rn"),
            {"r": "GetFunderByRoute", "rn": route_name},
        ).mappings().first()


# --------------------------
# Routes
# --------------------------
@app.get("/")
def index():
    # DRY: send users to the funders list
    return redirect(url_for("list_funders"))


@app.get("/funders")
def list_funders():
    funders = fetch_funders()
    return render_template("kmko_funders.html", funders=funders)

@app.route("/<route_name>", methods=["GET", "POST"])
def record_participation(route_name: str):
    funder = fetch_funder_by_route(route_name)
    if not funder:
        return render_template("kmko_not_found.html", route_name=route_name), 404

    funder_id = funder["FunderID"]

    if request.method == "POST":
        # Read fields
        first = (request.form.get("FirstName") or "").strip()
        last  = (request.form.get("LastName") or "").strip()
        dob   = (request.form.get("DateOfBirth") or "").strip()
        consent = request.form.get("Consent") == "yes"  # ✅ server-side consent check

        # Server-side validation
        errors = []
        if not first:   errors.append("First name is required.")
        if not last:    errors.append("Last name is required.")
        if not dob:     errors.append("Date of birth is required.")
        if not consent: errors.append("You must agree to the data use terms.")

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template("kmko_form.html", funder=funder, form=request.form), 400

        # Insert via stored proc (no row read)
        try:
            with get_engine().begin() as conn:
                conn.execute(
                    text("""
                        EXEC dbo.KMKO_HelperFunctions
                            @Request=:r,
                            @FunderID=:fid,
                            @FirstName=:fn,
                            @LastName=:ln,
                            @DateOfBirth=:dob
                    """),
                    {"r": "InsertParticipant", "fid": funder_id, "fn": first, "ln": last, "dob": dob}
                )

            # ✅ Redirect to success page
            return redirect(url_for("submission_success", route_name=route_name, first=first))


        except Exception as e:
            flash(f"Error saving record: {e}", "danger")
            return render_template("kmko_form.html", funder=funder, form=request.form), 500

    # GET
    return render_template("kmko_form.html", funder=funder, form={})

@app.get("/<route_name>/thanks")
def submission_success(route_name: str):
    funder = fetch_funder_by_route(route_name)
    first = (request.args.get("first") or "").strip() or None
    # if you later capture a PID, pass it too
    return render_template("kmko_success.html", funder=funder, first=first)

# --------------------------
# Error handlers
# --------------------------
@app.errorhandler(404)
def not_found(_e):
    return jsonify(error="not_found", message="The requested resource was not found."), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify(error="server_error", message=str(e)), 500


# --------------------------
# Local dev entrypoint
# --------------------------
if __name__ == "__main__":
    # In production, use: gunicorn run:app --bind 0.0.0.0:10000
    port = int(os.getenv("PORT", "10000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
