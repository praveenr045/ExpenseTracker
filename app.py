import os
from flask import Flask, request, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from dotenv import load_dotenv  # 👈 new

# Load .env file automatically
load_dotenv()

app = Flask(__name__)

# ================================
# 🔹 Google Sheets Setup
# ================================
CRED_FILE = os.getenv("GOOGLE_SHEETS_CRED")  # set this in your environment
if not CRED_FILE:
    raise ValueError("❌ GOOGLE_SHEETS_CRED environment variable not set!")

SPREADSHEET_NAME = "Expense Tracker"

scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]

creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, scope)
client = gspread.authorize(creds)


# ================================
# 🔹 Helper Functions
# ================================
def get_monthly_sheet(date_obj=None, month_year=None):
    """
    Get or create the sheet for the given month.
    - If month_year is provided (format YYYY-MM), use that.
    - Otherwise fallback to the date_obj or today.
    """
    ss = client.open(SPREADSHEET_NAME)

    if month_year:
        # Convert YYYY-MM -> datetime
        date_obj = datetime.strptime(month_year, "%Y-%m")
    elif not date_obj:
        date_obj = datetime.now()

    month_name = date_obj.strftime("%B %Y")  # e.g. "September 2025"

    try:
        sheet = ss.worksheet(month_name)
    except gspread.WorksheetNotFound:
        # Create if not exists
        sheet = ss.add_worksheet(title=month_name, rows="1000", cols="10")
        sheet.append_row(["Date", "Category", "Amount", "Note"])  # headers
    return sheet


def insert_expense(sheet, date_str, category, amount, note):
    """
    Insert an expense row at the correct chronological position.
    """
    all_values = sheet.get_all_values()
    if len(all_values) <= 1:
        # only header exists
        sheet.append_row([date_str, category, amount, note])
        return

    inserted = False
    for i in range(1, len(all_values)):
        try:
            existing_date = datetime.strptime(all_values[i][0], "%Y-%m-%d")
            new_date = datetime.strptime(date_str, "%Y-%m-%d")
        except:
            continue

        if new_date < existing_date:
            sheet.insert_row([date_str, category, amount, note], index=i+1)
            inserted = True
            break

    if not inserted:
        sheet.append_row([date_str, category, amount, note])


# ================================
# 🔹 API Routes
# ================================

@app.route("/add_expense", methods=["POST"])
def api_add_expense():
    """
    Add a new expense entry.
    JSON body:
    {
        "date": "2025-09-05",  # optional, default today
        "category": "Food",
        "amount": 250,
        "note": "Dinner"
    }
    """
    try:
        data = request.json
        date_str = data.get("date")
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        category = data.get("category", "Other")
        amount = float(data.get("amount", 0))
        note = data.get("note", "")

        # Get monthly sheet and insert
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        sheet = get_monthly_sheet(date_obj=date_obj)
        insert_expense(sheet, date_str, category, amount, note)

        return jsonify({"status": "ok", "message": "Expense added successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/get_summary", methods=["GET"])
def api_get_summary():
    """
    Category-wise summary for a given month.
    Pass ?month=YYYY-MM in the query string.
    """
    try:
        month_param = request.args.get("month")
        sheet = get_monthly_sheet(month_year=month_param)
        records = sheet.get_all_records()

        summary = {}
        for row in records:
            category = row.get("Category", "Other")
            try:
                amount = float(row.get("Amount", 0))
            except:
                amount = 0
            summary[category] = summary.get(category, 0) + amount

        return jsonify({"status": "ok", "summary": summary})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/get_daily_summary", methods=["GET"])
def api_get_daily_summary():
    """
    Daily spend summary for a given month.
    Pass ?month=YYYY-MM in the query string.
    """
    try:
        month_param = request.args.get("month")
        sheet = get_monthly_sheet(month_year=month_param)
        records = sheet.get_all_records()

        daily_summary = {}
        for row in records:
            date_str = row.get("Date")
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                day = date_obj.strftime("%d")  # e.g. "05"
                amount = float(row.get("Amount", 0))
            except:
                continue

            daily_summary[day] = daily_summary.get(day, 0) + amount

        sorted_summary = dict(sorted(daily_summary.items(), key=lambda x: int(x[0])))

        return jsonify({"status": "ok", "daily_summary": sorted_summary})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ================================
# 🔹 Run the Flask App
# ================================
if __name__ == "__main__":
    app.run(debug=True)
