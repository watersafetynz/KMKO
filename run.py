# run.py
import os
from flask import Flask, jsonify
from dotenv import load_dotenv
from db import get_engine

# Load environment variables from .env (safe to no-op in prod if not present)
load_dotenv()

app = Flask(__name__)
# --- add near the top of run.py ---
from flask import render_template, request, redirect, url_for, flash
from sqlalchemy import text

app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET", "dev-secret")  # for flash()

@app.get("/funders")
def list_funders():
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("EXEC dbo.KMKO_HelperFunctions @Request=:r"),
            {"r": "ListFunders"}
        ).mappings().all()
    return render_template("kmko_funders.html", funders=rows)


@app.route("/<route_name>/RecordParticipation", methods=["GET", "POST"])
def record_participation(route_name: str):
    with get_engine().connect() as conn:
        funder = conn.execute(
            text("EXEC dbo.KMKO_HelperFunctions @Request=:r, @RouteName=:rn"),
            {"r": "GetFunderByRoute", "rn": route_name}
        ).mappings().first()

    if not funder:
        return render_template("kmko_not_found.html", route_name=route_name), 404

    funder_id = funder["FunderID"]

    if request.method == "POST":
        first = (request.form.get("FirstName") or "").strip()
        last  = (request.form.get("LastName") or "").strip()
        dob   = (request.form.get("DateOfBirth") or "").strip()
        consent = request.form.get("Consent") == "yes"

        errors = []
        if not first: errors.append("First name is required.")
        if not last:  errors.append("Last name is required.")
        if not dob:   errors.append("Date of birth is required.")
        if not consent: errors.append("You must agree to data use terms.")

        if errors:
            for e in errors: flash(e, "danger")
            return render_template("kmko_form.html", funder=funder, form=request.form), 400

        try:
            with get_engine().begin() as conn:
                row = conn.execute(
                    text("""
                        EXEC dbo.KMKO_HelperFunctions
                            @Request=:r,
                            @FunderID=:fid,
                            @FirstName=:fn,
                            @LastName=:ln,
                            @DateOfBirth=:dob,
                            @ConsentGiven=:cg
                    """),
                    {"r": "InsertParticipant", "fid": funder_id, "fn": first,
                     "ln": last, "dob": dob, "cg": 1 if consent else 0}
                ).mappings().first()
                new_id = row["ParticipantID"] if row and "ParticipantID" in row else None
            flash("Participation recorded ✅", "success")
            return redirect(url_for("record_participation", route_name=route_name) + f"?saved=1&pid={new_id or ''}")
        except Exception as e:
            flash(f"Error saving record: {e}", "danger")
            return render_template("kmko_form.html", funder=funder, form=request.form), 500

    return render_template("kmko_form.html", funder=funder, form={})


# ---- Routes ----
@app.get("/")
def index():
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("EXEC dbo.KMKO_HelperFunctions @Request=:r"),
            {"r": "ListFunders"}
        ).mappings().all()
    return render_template("kmko_funders.html", funders=rows)


# ---- Error handlers (optional but handy) ----
@app.errorhandler(404)
def not_found(_e):
    return jsonify(error="not_found", message="The requested resource was not found."), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify(error="server_error", message=str(e)), 500


# ---- Local dev entrypoint ----
if __name__ == "__main__":
    # For local testing only. In production, use gunicorn:
    #   gunicorn run:app --bind 0.0.0.0:10000
    port = int(os.getenv("PORT", "10000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
