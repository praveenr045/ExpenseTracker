import os
import traceback
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread_formatting import (
    CellFormat, TextFormat, Borders, Border, format_cell_range
)

load_dotenv()
app = Flask(__name__)

# ================================
# Config
# ================================
CRED_PATH = os.getenv("GOOGLE_SHEETS_CRED")  # absolute path to your service account .json
if not CRED_PATH:
    raise ValueError("GOOGLE_SHEETS_CRED environment variable is not set")

SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "Expense Tracker")  # change if needed

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

# ================================
# Google Sheets helpers
# ================================
def get_client():
    creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_PATH, SCOPES)
    return gspread.authorize(creds)

def get_spreadsheet():
    client = get_client()
    return client.open(SPREADSHEET_NAME)

def month_title_from_date(dt: datetime) -> str:
    return dt.strftime("%B %Y")  # e.g., "September 2025"

def month_title_from_param(month_param: str | None) -> str:
    # month_param format: "YYYY-MM" or None (use current month)
    if month_param:
        dt = datetime.strptime(month_param, "%Y-%m")
    else:
        dt = datetime.now()
    return month_title_from_date(dt)

def ensure_header(ws):
    """
    Ensure the header row exists and is correctly formatted.
    Safe even if the sheet was just created.
    """
    # Read first row (could be empty)
    values = ws.get_all_values()
    header_ok = False
    if values:
        first_row = values[0] if len(values) >= 1 else []
        # Normalize missing columns
        first_row += [""] * (4 - len(first_row))
        if first_row[:4] == ["Date", "Category", "Amount", "Note"]:
            header_ok = True

    if not header_ok:
        # Clear A1:D1 then set header
        ws.update("A1:D1", [["Date", "Category", "Amount", "Note"]])

    # Apply formatting (bold + fontSize=12 + bottom border)
    try:
        header_fmt = CellFormat(
            textFormat=TextFormat(bold=True, fontSize=11, fontFamily="Comic Sans MS"),
            borders=Borders(bottom=Border("SOLID"))
        )
        format_cell_range(ws, "A1:D1", header_fmt)
    except Exception:
        # Don't break if formatting fails
        traceback.print_exc()

def get_or_create_worksheet(month_title: str):
    ss = get_spreadsheet()
    try:
        ws = ss.worksheet(month_title)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=month_title, rows="1000", cols="10")
    # Always ensure header exists & is formatted
    ensure_header(ws)
    return ws

# ================================
# Core insert/update helpers
# ================================
def parse_amount(val):
    try:
        return float(val)
    except Exception:
        return 0.0

def clean_str(s):
    return (s or "").strip()

def add_or_update_expense(date_str, category, amount, note):
    """
    - Reject future dates
    - If exact duplicate exists (Date, Category, Amount, Note) -> error
    - Else if same Date+Category exists -> update that row's Amount & Note
    - Else insert in correct chronological order
    """
    # Parse & validate date
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    if date_obj.date() > datetime.today().date():
        return {"status": "error", "message": "Future dates are not allowed. Use today or past dates."}

    month_title = month_title_from_date(date_obj)
    ws = get_or_create_worksheet(month_title)

    # Normalize fields
    category = clean_str(category)
    note = clean_str(note)
    amount = parse_amount(amount)

    # Fetch entire sheet to:
    # - check duplicates
    # - detect same date+category (for update)
    # - compute chronological insert index
    all_values = ws.get_all_values()  # includes header if present
    if not all_values:
        # Extremely fresh sheet; ensure header then append
        ensure_header(ws)
        ws.append_row([date_str, category, amount, note])
        return {"status": "ok", "action": "Expense added successfully!"}

    # Trackers
    duplicate_found = False
    update_row_index = None  # 1-based in Sheets
    insert_index = None

    # Iterate rows starting from row 2 (skip header)
    for idx in range(1, len(all_values)):
        row = all_values[idx] if idx < len(all_values) else []
        # Defensive: pad row to 4 cols
        row += [""] * (4 - len(row))

        row_date = clean_str(row[0])
        row_category = clean_str(row[1])
        row_amount = parse_amount(row[2])
        row_note = clean_str(row[3])

        # Compute chronological insertion position
        try:
            existing_dt = datetime.strptime(row_date, "%Y-%m-%d")
            if insert_index is None and date_obj < existing_dt:
                # Insert BEFORE this row; +1 because Sheets is 1-based
                insert_index = idx + 1
        except Exception:
            # If date parsing fails, ignore for sort purposes
            pass

        # Exact duplicate?
        if (row_date == date_str
            and row_category.lower() == category.lower()
            and row_amount == amount
            and row_note == note):
            duplicate_found = True
            break

        # Same Date + Category (case-insensitive)?
        if update_row_index is None and (row_date == date_str and row_category.lower() == category.lower()):
            update_row_index = idx + 1  # 1-based index for Sheets

    if duplicate_found:
        return {"status": "error", "message": "Duplicate expense found. Entry not added."}

    if update_row_index:
        # Update Amount & Note on the existing row
        ws.update_cell(update_row_index, 3, amount)  # Amount (col C)
        ws.update_cell(update_row_index, 4, note)    # Note (col D)
        return {"status": "ok", "action": "Updated the existing expense!"}

    # Insert new row in chronological order
    new_row = [date_str, category, amount, note]
    if insert_index:
        ws.insert_row(new_row, insert_index)
        return {"status": "ok", "action": "Inserted the new expense!"}
    else:
        ws.append_row(new_row)
        return {"status": "ok", "action": "Expense added successfully!"}

# ================================
# Routes
# ================================
@app.route("/add_expense", methods=["POST"])
def add_expense():
    try:
        data = request.get_json(force=True) or {}
        date_str = clean_str(data.get("date")) or datetime.today().strftime("%Y-%m-%d")
        category = data.get("category", "Misc")
        amount = data.get("amount", 0)
        note = data.get("note", "")

        result = add_or_update_expense(date_str, category, amount, note)
        return jsonify(result), (200 if result.get("status") == "ok" else 400)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/get_summary", methods=["GET"])
def get_summary():
    try:
        month_param = request.args.get("month")  # YYYY-MM (optional; defaults to current)
        month_title = month_title_from_param(month_param)

        ws = get_or_create_worksheet(month_title)
        records = ws.get_all_records()  # uses header row keys

        summary = {}
        for r in records:
            cat = clean_str(r.get("Category"))
            amt = parse_amount(r.get("Amount"))
            summary[cat] = summary.get(cat, 0.0) + amt

        return jsonify({"status": "ok", "summary": summary})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/get_daily_summary", methods=["GET"])
def get_daily_summary():
    try:
        month_param = request.args.get("month")  # YYYY-MM (optional; defaults to current)
        month_title = month_title_from_param(month_param)

        ws = get_or_create_worksheet(month_title)
        records = ws.get_all_records()

        daily = {}
        for r in records:
            date_str = clean_str(r.get("Date"))
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d")
                day_key = d.strftime("%d")  # "01".."31"
                amt = parse_amount(r.get("Amount"))
                daily[day_key] = daily.get(day_key, 0.0) + amt
            except Exception:
                continue

        # Sort by numeric day
        daily_sorted = {k: daily[k] for k in sorted(daily.keys(), key=lambda x: int(x))}
        return jsonify({"status": "ok", "daily_summary": daily_sorted})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# ================================
# App
# ================================
if __name__ == "__main__":
    app.run(debug=True)
