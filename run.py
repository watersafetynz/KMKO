# run.py
import os
import io
import re
from typing import Optional, List, Dict
from datetime import datetime, date
import json

from flask import (
    Flask, jsonify, render_template, request, redirect, url_for, flash
)
from dotenv import load_dotenv
from sqlalchemy import text
import pandas as pd
from dateutil import parser as dateparser  # installed with pandas

# --------------------------
# App setup
# --------------------------
load_dotenv()  # load env vars early
from db import get_engine  # noqa: E402  (import after load_dotenv)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET", "dev-secret")

# Optional: cap CSV size (bytes). Set env MAX_CSV_MB to override (default 5 MB).
MAX_CSV_BYTES = int(float(os.getenv("MAX_CSV_MB", "5")) * 1024 * 1024)

# Two-digit year pivot (e.g., 00..30 → 2000..2030; 31..99 → 1931..1999)
YY_PIVOT = 30

# --------------------------
# DB helpers
# --------------------------
def fetch_funders() -> List[Dict]:
    """
    Return all funders (FunderID, Description, RouteName[, BulkUpload]).
    The stored proc should include BulkUpload if you want to show it in the list.
    """
    with get_engine().connect() as conn:
        return (
            conn.execute(
                text("EXEC dbo.KMKO_HelperFunctions @Request=:r"),
                {"r": "ListFunders"},
            )
            .mappings()
            .all()
        )


def fetch_funder_by_route(route_name: str) -> Optional[dict]:
    """Return a single funder by RouteName, or None if not found."""
    with get_engine().connect() as conn:
        return (
            conn.execute(
                text(
                    "EXEC dbo.KMKO_HelperFunctions @Request=:r, @RouteName=:rn"
                ),
                {"r": "GetFunderByRoute", "rn": route_name},
            )
            .mappings()
            .first()
        )

# --------------------------
# Date parsing helpers
# --------------------------
def _apply_century_sanity(dt: datetime) -> datetime:
    """If parsed date is in the future (vs today), roll back 100 years."""
    today = date.today()
    if dt.date() > today:
        try:
            return dt.replace(year=dt.year - 100)
        except ValueError:
            # e.g., Feb 29 on a non-leap year; nudge to Feb 28
            return dt.replace(year=dt.year - 100, month=2, day=28)
    return dt


def _parse_digits_compact(s: str, prefer_day_first: bool) -> Optional[datetime]:
    """
    Handle compact digit-only forms (no separators):
      - 8 digits: try DDMMYYYY / YYYYMMDD / MMDDYYYY (pref ordered by prefer_day_first)
      - 6 digits: try DDMMYY or MMDDYY using YY_PIVOT (pref ordered by prefer_day_first)
    Return datetime or None if not applicable/valid.
    """
    if not s.isdigit():
        return None

    def build(y, m, d):
        y, m, d = int(y), int(m), int(d)
        return datetime(y, m, d)  # raises ValueError if invalid

    if len(s) == 8:
        # Prepare attempt orders based on preference
        if prefer_day_first:
            attempts = [
                ("DDMMYYYY", (s[4:8], s[2:4], s[0:2])),  # y, m, d
                ("YYYYMMDD", (s[0:4], s[4:6], s[6:8])),
                ("MMDDYYYY", (s[4:8], s[0:2], s[2:4])),
            ]
        else:
            attempts = [
                ("MMDDYYYY", (s[4:8], s[0:2], s[2:4])),
                ("YYYYMMDD", (s[0:4], s[4:6], s[6:8])),
                ("DDMMYYYY", (s[4:8], s[2:4], s[0:2])),
            ]
        for _, (yy, mm, dd) in attempts:
            try:
                return build(yy, mm, dd)
            except ValueError:
                continue
        return None

    if len(s) == 6:
        # Two-digit year with pivot
        def year_from_yy(yy2: str) -> int:
            val = int(yy2)
            return 2000 + val if val <= YY_PIVOT else 1900 + val

        orders = [
            ("DDMMYY", (year_from_yy(s[4:6]), s[2:4], s[0:2])),
            ("MMDDYY", (year_from_yy(s[4:6]), s[0:2], s[2:4])),
        ]
        if not prefer_day_first:
            orders.reverse()

        for _, (yyyy, mm, dd) in orders:
            try:
                return build(yyyy, mm, dd)
            except ValueError:
                continue
        return None

    return None


def parse_any_date(dob_str: str, prefer_day_first: bool = True) -> str:
    """
    Parse a wide range of DOB formats and return ISO 'YYYY-MM-DD'.

    Accepts '-', '/', '.', spaces as separators; supports month names;
    defaults to day-first (NZ); auto-flips to month-first when needed;
    handles 2-digit years with pivot; adjusts future dates by -100 years.
    """
    s = (dob_str or "").strip()
    if not s:
        raise ValueError("Date is required")

    # Fast path for compact digits (includes DDMMYYYY / YYYYMMDD / MMDDYYYY, etc.)
    digits_only = re.sub(r"\D", "", s)
    try:
        dt = _parse_digits_compact(digits_only, prefer_day_first)
        if dt:
            dt = _apply_century_sanity(dt)
            return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass  # fall through to general parsing

    # General parsing with dateutil, try day-first then month-first
    try:
        dt = dateparser.parse(s, dayfirst=prefer_day_first, yearfirst=False, fuzzy=True)
    except Exception:
        try:
            dt = dateparser.parse(s, dayfirst=not prefer_day_first, yearfirst=False, fuzzy=True)
        except Exception:
            raise ValueError(
                f"Invalid date format: {dob_str!r}. Try like '14/02/2013' or '2013-02-14'."
            )

    dt = _apply_century_sanity(dt)
    return dt.strftime("%Y-%m-%d")

# --------------------------
# CSV helpers (aliases + headerless fallback)
# --------------------------
REQUIRED_COLS = ["FirstName", "LastName", "DateOfBirth"]

HEADER_ALIASES = {
    # FirstName
    "firstname": "FirstName", "first_name": "FirstName", "givenname": "FirstName", "given_name": "FirstName",
    # LastName
    "lastname": "LastName", "last_name": "LastName", "surname": "LastName", "familyname": "LastName", "family_name": "LastName",
    # DateOfBirth
    "dob": "DateOfBirth", "dateofbirth": "DateOfBirth", "date_of_birth": "DateOfBirth",
    "birthdate": "DateOfBirth", "birth_date": "DateOfBirth",
}

def _canon_header(name: str) -> Optional[str]:
    key = re.sub(r"[^a-z0-9]", "", str(name).lower())
    return HEADER_ALIASES.get(key)

def load_csv_flex(file_storage) -> pd.DataFrame:
    """
    Returns a DataFrame with canonical columns: FirstName, LastName, DateOfBirth.
    - Accepts any order via alias mapping
    - Accepts headerless CSVs (assumes first 3 columns are the required fields)
    - Extra columns are ignored
    """
    file_storage.stream.seek(0)
    text_data = file_storage.stream.read().decode("utf-8", errors="ignore")

    # Try headered first
    df = pd.read_csv(io.StringIO(text_data), dtype=str).fillna("")
    df.columns = [_canon_header(c) or c for c in df.columns]
    if all(c in df.columns for c in REQUIRED_COLS):
        return df[REQUIRED_COLS].copy()

    # Fallback: headerless -> take first three columns
    df2 = pd.read_csv(io.StringIO(text_data), dtype=str, header=None).fillna("")
    if df2.shape[1] >= 3:
        df2 = df2.rename(columns={0: "FirstName", 1: "LastName", 2: "DateOfBirth"})
        return df2[REQUIRED_COLS].copy()

    raise ValueError(
        "Missing required columns. Expect FirstName, LastName, DateOfBirth "
        "(with headers in any order or as the first three columns)."
    )

def validate_row(row: dict) -> List[str]:
    """Return a list of validation error messages for a row dict."""
    errs: List[str] = []
    if not (row.get("FirstName") or "").strip():
        errs.append("FirstName is required")
    if not (row.get("LastName") or "").strip():
        errs.append("LastName is required")
    if not (row.get("DateOfBirth") or "").strip():
        errs.append("DateOfBirth is required")

    # date format check
    if (row.get("DateOfBirth") or "").strip():
        try:
            parse_any_date(row["DateOfBirth"])
        except ValueError as e:
            errs.append(str(e))

    return errs

# --------------------------
# Routes
# --------------------------
@app.get("/")
def index():
    return redirect(url_for("list_funders"))

@app.get("/funders")
def list_funders():
    funders = fetch_funders()
    return render_template("kmko_funders.html", funders=funders)

@app.route("/<route_name>", methods=["GET", "POST"])
def record_participation(route_name: str):
    """
    Single route for both single-entry and bulk CSV upload paths.
    Branches on funder['BulkUpload'].
    """
    funder = fetch_funder_by_route(route_name)  # must return BulkUpload flag
    if not funder:
        return render_template("kmko_not_found.html", route_name=route_name), 404

    funder_id = funder["FunderID"]
    is_bulk = bool(funder.get("BulkUpload", 0))

    # -------- GET --------
    if request.method == "GET":
        if is_bulk:
            return render_template("kmko_bulk_upload.html", funder=funder)
        return render_template("kmko_form.html", funder=funder, form={})

    # -------- POST --------
    if is_bulk:
        # ✅ Enforce consent in bulk mode (checkbox must be inside the form)
        bulk_consent = (request.form.get("Consent") == "yes")
        if not bulk_consent:
            flash("You must agree to the data use terms before uploading.", "danger")
            return render_template("kmko_bulk_upload.html", funder=funder), 400

        # CSV upload branch
        file = request.files.get("csv_file")
        if not file or file.filename == "":
            flash("Please choose a CSV file to upload.", "danger")
            return render_template("kmko_bulk_upload.html", funder=funder), 400

        # Optional file size cap
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        if MAX_CSV_BYTES and size > MAX_CSV_BYTES:
            flash("File is too large. Please keep it under the configured limit.", "danger")
            return render_template("kmko_bulk_upload.html", funder=funder), 400

        try:
            # Flexible read (aliases + headerless)
            df = load_csv_flex(file)

            # Trim whitespace
            for c in df.columns:
                df[c] = df[c].map(lambda x: x.strip() if isinstance(x, str) else x)

            errors: List[tuple] = []
            to_insert: List[Dict] = []

            for idx, row in df.iterrows():
                row_dict = {c: row.get(c, "") for c in REQUIRED_COLS}
                row_errs = validate_row(row_dict)
                if row_errs:
                    errors.append((idx + 2, row_errs))  # +2: header + 1-based idx for UX
                    continue

                to_insert.append(
                    {
                        "FirstName": row_dict["FirstName"],
                        "LastName": row_dict["LastName"],
                        "DateISO": parse_any_date(row_dict["DateOfBirth"]),
                    }
                )

            if errors:
                # Summarize first ~10 error rows to keep flash readable
                preview = "; ".join(
                    [f"Row {r}: {', '.join(es)}" for r, es in errors[:10]]
                )
                more = "" if len(errors) <= 10 else f" (+{len(errors) - 10} more rows)"
                flash(
                    f"Validation failed for {len(errors)} row(s): {preview}{more}",
                    "danger",
                )
                return render_template("kmko_bulk_upload.html", funder=funder), 400

            # Insert all valid rows in one DB call via JSON payload
            if not to_insert:
                flash("No valid rows to upload after validation.", "danger")
                return render_template("kmko_bulk_upload.html", funder=funder), 400

            payload = json.dumps([
                {
                    "FirstName": rec["FirstName"],
                    "LastName":  rec["LastName"],
                    "DateISO":   rec["DateISO"],
                    "Consent":   bulk_consent,  # carry through per-record (optional)
                }
                for rec in to_insert
            ])

            with get_engine().begin() as conn:
                conn.execute(
                    text("""
                        EXEC dbo.KMKO_HelperFunctions
                            @Request=:r,
                            @FunderID=:fid,
                            @Payload=:payload,
                            @ConsentGiven=:cg
                    """),
                    {
                        "r": "BulkInsertParticipantsJson",
                        "fid": funder_id,
                        "payload": payload,
                        "cg": bulk_consent,   # ✅ pass consent flag to SP
                    },
                )

            inserted = len(to_insert)
            flash(f"Uploaded {inserted} participant(s) successfully.", "success")
            return render_template("kmko_bulk_upload.html", funder=funder)

        except Exception as e:
            flash(f"Error processing CSV: {e}", "danger")
            return render_template("kmko_bulk_upload.html", funder=funder), 500

    # Single-form branch
    first = (request.form.get("FirstName") or "").strip()
    last = (request.form.get("LastName") or "").strip()
    dob = (request.form.get("DateOfBirth") or "").strip()
    consent = request.form.get("Consent") == "yes"  # server-side consent check

    # Server-side validation
    errors: List[str] = []
    if not first:
        errors.append("First name is required.")
    if not last:
        errors.append("Last name is required.")
    if not dob:
        errors.append("Date of birth is required.")
    if not consent:
        errors.append("You must agree to the data use terms.")

    # Parse date early to fail fast with a helpful message
    iso_dob: Optional[str] = None
    if dob:
        try:
            iso_dob = parse_any_date(dob)
        except ValueError as e:
            errors.append(str(e))

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("kmko_form.html", funder=funder, form=request.form), 400

    # Insert via stored proc (no row read)
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    """
                    EXEC dbo.KMKO_HelperFunctions
                        @Request=:r,
                        @FunderID=:fid,
                        @FirstName=:fn,
                        @LastName=:ln,
                        @DateOfBirth=:dob,
                        @ConsentGiven=:cg
                    """
                ),
                {
                    "r": "InsertParticipant",
                    "fid": funder_id,
                    "fn": first,
                    "ln": last,
                    "dob": iso_dob,
                    "cg": consent,
                },
            )
        return redirect(url_for("submission_success", route_name=route_name, first=first))
    except Exception as e:
        flash(f"Error saving record: {e}", "danger")
        return render_template("kmko_form.html", funder=funder, form=request.form), 500


@app.get("/<route_name>/thanks")
def submission_success(route_name: str):
    funder = fetch_funder_by_route(route_name)
    first = (request.args.get("first") or "").strip() or None
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
