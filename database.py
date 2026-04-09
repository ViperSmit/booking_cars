import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
import hashlib
import os
from pathlib import Path
import secrets

DB_PATH = Path(__file__).with_name("database.db")
SEED_PATH = Path(__file__).with_name("cars_seed.json")
SEED_ALIASES = {
    "VW Atlas Grey": ["VW Atlas", "Atlas Grey"],
    "Tesla 3": ["TESLA 3"],
    "Touareg Blue": ["Touareg"],
}

EXPENSE_BREAKDOWN_CATEGORIES = ("washing", "service", "oil", "tires", "insurance", "fines")
SERVICE_SOON_THRESHOLD_KM = 500
SERVICE_WARNING_THRESHOLD_KM = 5000
SERVICE_CRITICAL_THRESHOLD_KM = 1000


def generate_password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 260000)
    return f"{salt}${digest.hex()}"


def check_password_hash(stored_hash, password):
    try:
        salt, digest = stored_hash.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 260000)
    return secrets.compare_digest(digest, candidate.hex())


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def load_seed_cars():
    if not SEED_PATH.exists():
        return []

    with SEED_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_seed_cars(cars):
    with SEED_PATH.open("w", encoding="utf-8") as file:
        json.dump(cars, file, ensure_ascii=False, indent=2)
        file.write("\n")


def ensure_car_schema(cursor):
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(cars)").fetchall()}
    additions = {
        "year": "INTEGER",
        "car_number": "TEXT",
        "insurance_policy_number": "TEXT",
        "insurance_valid_until": "TEXT",
        "inspection_valid_until": "TEXT",
        "service_due_date": "TEXT",
        "price_1_3": "REAL",
        "price_4_7": "REAL",
        "price_8_14": "REAL",
        "current_mileage": "INTEGER",
        "last_service_mileage": "INTEGER",
        "service_interval_km": "INTEGER",
        "rental_contract_file": "TEXT",
        "rental_contract_url": "TEXT",
        "insurance_file": "TEXT",
        "inspection_file": "TEXT",
    }

    for column, column_type in additions.items():
        if column not in columns:
            cursor.execute(f"ALTER TABLE cars ADD COLUMN {column} {column_type}")

    if "car_number" in additions:
        cursor.execute(
            """
            UPDATE cars
            SET car_number = COALESCE(car_number, plate)
            WHERE plate IS NOT NULL
            """
        )


def ensure_booking_schema(cursor):
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(bookings)").fetchall()}
    if "booking_time" not in columns:
        cursor.execute("ALTER TABLE bookings ADD COLUMN booking_time TEXT")


def ensure_audit_log_schema(cursor):
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(audit_logs)").fetchall()}
    if "username" not in columns:
        cursor.execute("ALTER TABLE audit_logs ADD COLUMN username TEXT")


def compute_document_status(valid_until, active_label, warning_label, expired_label):
    if not valid_until:
        return {
            "label": "Нет данных",
            "color": "#64748b",
            "code": "unknown",
        }

    try:
        expiry = date.fromisoformat(valid_until)
    except ValueError:
        return {
            "label": "Некорректная дата",
            "color": "#64748b",
            "code": "unknown",
        }

    delta = (expiry - date.today()).days
    if delta < 0:
        return {
            "label": f"{expired_label} {expiry.isoformat()}",
            "color": "#dc2626",
            "code": "expired",
        }
    if delta <= 30:
        return {
            "label": f"{warning_label} {expiry.isoformat()}",
            "color": "#f59e0b",
            "code": "warning",
        }
    return {
        "label": f"{active_label} {expiry.isoformat()}",
        "color": "#16a34a",
        "code": "active",
    }


def compute_service_mileage_status(current_mileage, last_service_mileage, service_interval_km):
    current_mileage = safe_int(current_mileage)
    last_service_mileage = safe_int(last_service_mileage)
    service_interval_km = safe_int(service_interval_km)

    if not service_interval_km:
        return {
            "label": "Не задан интервал ТО",
            "color": "#64748b",
            "code": "unknown",
            "next_service_mileage": 0,
            "remaining_km": 0,
        }

    if not last_service_mileage:
        return {
            "label": "Не задан пробег последнего ТО",
            "color": "#64748b",
            "code": "unknown",
            "next_service_mileage": 0,
            "remaining_km": 0,
        }

    next_service_mileage = last_service_mileage + service_interval_km
    remaining_km = next_service_mileage - current_mileage
    if remaining_km < SERVICE_CRITICAL_THRESHOLD_KM:
        return {
            "label": f"Срочно на ТО, осталось {max(remaining_km, 0)} км",
            "color": "#dc2626",
            "code": "expired",
            "next_service_mileage": next_service_mileage,
            "remaining_km": remaining_km,
        }
    if remaining_km < SERVICE_WARNING_THRESHOLD_KM:
        return {
            "label": f"Скоро ТО, осталось {remaining_km} км",
            "color": "#f59e0b",
            "code": "warning",
            "next_service_mileage": next_service_mileage,
            "remaining_km": remaining_km,
        }
    return {
        "label": f"ТО на {next_service_mileage} км",
        "color": "#16a34a",
        "code": "active",
        "next_service_mileage": next_service_mileage,
        "remaining_km": remaining_km,
    }


def ensure_analytics_tables(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_expenses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL,
            date TEXT,
            amount REAL DEFAULT 0,
            notes TEXT,
            category TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            mileage INTEGER,
            vendor TEXT,
            FOREIGN KEY (vehicle_id) REFERENCES cars(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS service_records(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL,
            date TEXT,
            amount REAL DEFAULT 0,
            notes TEXT,
            category TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            mileage INTEGER,
            next_service_mileage INTEGER,
            provider TEXT,
            FOREIGN KEY (vehicle_id) REFERENCES cars(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tire_changes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL,
            date TEXT,
            amount REAL DEFAULT 0,
            notes TEXT,
            category TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            mileage INTEGER,
            next_change_mileage INTEGER,
            tire_brand TEXT,
            FOREIGN KEY (vehicle_id) REFERENCES cars(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS insurance_records(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL,
            date TEXT,
            amount REAL DEFAULT 0,
            notes TEXT,
            category TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expiry_date TEXT,
            policy_number TEXT,
            FOREIGN KEY (vehicle_id) REFERENCES cars(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fines(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL,
            date TEXT,
            amount REAL DEFAULT 0,
            notes TEXT,
            category TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reference_number TEXT,
            status TEXT,
            FOREIGN KEY (vehicle_id) REFERENCES cars(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS mileage_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL,
            date TEXT,
            amount REAL DEFAULT 0,
            notes TEXT,
            category TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            mileage INTEGER,
            source TEXT,
            FOREIGN KEY (vehicle_id) REFERENCES cars(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS inspection_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL,
            date TEXT,
            amount REAL DEFAULT 0,
            notes TEXT,
            category TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            due_date TEXT,
            mileage INTEGER,
            result TEXT,
            FOREIGN KEY (vehicle_id) REFERENCES cars(id) ON DELETE CASCADE
        )
        """
    )

    index_specs = (
        ("vehicle_expenses", "vehicle_id, date"),
        ("service_records", "vehicle_id, date"),
        ("tire_changes", "vehicle_id, date"),
        ("insurance_records", "vehicle_id, date"),
        ("fines", "vehicle_id, date"),
        ("mileage_logs", "vehicle_id, date"),
        ("inspection_logs", "vehicle_id, date"),
    )
    for table_name, columns in index_specs:
        cursor.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_vehicle_date
            ON {table_name}({columns})
            """
        )


def normalize_car_row(row):
    insurance_status = compute_document_status(
        row["insurance_valid_until"],
        "Действует до",
        "Скоро истекает",
        "Истекла",
    )
    inspection_status = compute_document_status(
        row["inspection_valid_until"],
        "Техосмотр до",
        "Скоро истекает ТО",
        "ТО истек",
    )
    service_status = compute_service_mileage_status(
        row["current_mileage"],
        row["last_service_mileage"],
        row["service_interval_km"],
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "year": row["year"],
        "car_number": row["car_number"] or "",
        "vin": row["vin"] or "",
        "passport": row["passport"] or "",
        "insurance_policy_number": row["insurance_policy_number"] or "",
        "insurance_valid_until": row["insurance_valid_until"] or "",
        "inspection_valid_until": row["inspection_valid_until"] or "",
        "service_due_date": row["service_due_date"] or "",
        "description": row["description"] or "",
        "insurance_status_label": insurance_status["label"],
        "insurance_status_color": insurance_status["color"],
        "insurance_status_code": insurance_status["code"],
        "inspection_status_label": inspection_status["label"],
        "inspection_status_color": inspection_status["color"],
        "inspection_status_code": inspection_status["code"],
        "service_status_label": service_status["label"],
        "service_status_color": service_status["color"],
        "service_status_code": service_status["code"],
        "next_service_mileage": service_status["next_service_mileage"],
        "service_remaining_km": service_status["remaining_km"],
        "price_1_3": row["price_1_3"],
        "price_4_7": row["price_4_7"],
        "price_8_14": row["price_8_14"],
        "current_mileage": row["current_mileage"] or 0,
        "last_service_mileage": row["last_service_mileage"] or 0,
        "service_interval_km": row["service_interval_km"] or 0,
        "rental_contract_file": row["rental_contract_file"] or "",
        "rental_contract_url": row["rental_contract_url"] or "",
        "insurance_file": row["insurance_file"] or "",
        "inspection_file": row["inspection_file"] or "",
    }


def parse_iso_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def safe_float(value):
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value):
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_divide(numerator, denominator):
    if not denominator:
        return 0.0
    return numerator / denominator


def daterange(start_date, end_date):
    current = start_date
    while current < end_date:
        yield current
        current += timedelta(days=1)


def overlaps(start_a, end_a, start_b, end_b):
    return start_a < end_b and end_a > start_b


def clip_range(start_a, end_a, start_b, end_b):
    if not overlaps(start_a, end_a, start_b, end_b):
        return None, None
    return max(start_a, start_b), min(end_a, end_b)


def month_start(input_date):
    return input_date.replace(day=1)


def add_month(input_date):
    if input_date.month == 12:
        return input_date.replace(year=input_date.year + 1, month=1, day=1)
    return input_date.replace(month=input_date.month + 1, day=1)


def format_money(value):
    return round(safe_float(value), 2)


def format_percentage(value):
    return round(safe_float(value), 2)


def format_date_or_default(value, default="Не задано"):
    parsed = parse_iso_date(value)
    return parsed.isoformat() if parsed else default


def build_time_buckets(start_date, end_date):
    total_days = max((end_date - start_date).days, 1)
    buckets = []

    if total_days <= 45:
        current = start_date
        while current < end_date:
            next_date = current + timedelta(days=1)
            buckets.append(
                {
                    "key": current.isoformat(),
                    "label": current.strftime("%d %b"),
                    "start": current,
                    "end": next_date,
                }
            )
            current = next_date
        return buckets

    if total_days <= 180:
        current = start_date
        while current < end_date:
            next_date = min(current + timedelta(days=7), end_date)
            buckets.append(
                {
                    "key": current.isoformat(),
                    "label": f"{current.strftime('%d %b')} - {(next_date - timedelta(days=1)).strftime('%d %b')}",
                    "start": current,
                    "end": next_date,
                }
            )
            current = next_date
        return buckets

    current = month_start(start_date)
    while current < end_date:
        next_date = add_month(current)
        bucket_start = max(current, start_date)
        bucket_end = min(next_date, end_date)
        buckets.append(
            {
                "key": current.isoformat(),
                "label": current.strftime("%b %Y"),
                "start": bucket_start,
                "end": bucket_end,
            }
        )
        current = next_date
    return buckets


def serialize_chart_dataset(labels, data, empty_message="Нет данных"):
    cleaned_data = [round(safe_float(item), 2) for item in data]
    has_data = any(value != 0 for value in cleaned_data)
    return {
        "labels": labels,
        "data": cleaned_data,
        "is_empty": not has_data,
        "empty_message": empty_message,
    }


def load_analytics_records(conn):
    data = {
        "cars": conn.execute(
            """
            SELECT id, name, year, car_number, vin, passport, insurance_policy_number,
                   insurance_valid_until, inspection_valid_until, service_due_date, description,
                   price_1_3, price_4_7, price_8_14, current_mileage, last_service_mileage, service_interval_km,
                   rental_contract_file, rental_contract_url, insurance_file, inspection_file
            FROM cars
            ORDER BY name
            """
        ).fetchall(),
        "bookings": conn.execute(
            """
            SELECT id, car_id, client, booking_time, phone, start, end, price, deposit, note, status
            FROM bookings
            ORDER BY start
            """
        ).fetchall(),
        "vehicle_expenses": conn.execute(
            """
            SELECT id, vehicle_id, date, amount, notes, category, created_at, mileage, vendor
            FROM vehicle_expenses
            ORDER BY date, id
            """
        ).fetchall(),
        "service_records": conn.execute(
            """
            SELECT id, vehicle_id, date, amount, notes, category, created_at, mileage, next_service_mileage, provider
            FROM service_records
            ORDER BY date, id
            """
        ).fetchall(),
        "tire_changes": conn.execute(
            """
            SELECT id, vehicle_id, date, amount, notes, category, created_at, mileage, next_change_mileage, tire_brand
            FROM tire_changes
            ORDER BY date, id
            """
        ).fetchall(),
        "insurance_records": conn.execute(
            """
            SELECT id, vehicle_id, date, amount, notes, category, created_at, expiry_date, policy_number
            FROM insurance_records
            ORDER BY date, id
            """
        ).fetchall(),
        "fines": conn.execute(
            """
            SELECT id, vehicle_id, date, amount, notes, category, created_at, reference_number, status
            FROM fines
            ORDER BY date, id
            """
        ).fetchall(),
        "mileage_logs": conn.execute(
            """
            SELECT id, vehicle_id, date, amount, notes, category, created_at, mileage, source
            FROM mileage_logs
            ORDER BY date, id
            """
        ).fetchall(),
        "inspection_logs": conn.execute(
            """
            SELECT id, vehicle_id, date, amount, notes, category, created_at, due_date, mileage, result
            FROM inspection_logs
            ORDER BY date, id
            """
        ).fetchall(),
    }
    return {key: [dict(row) for row in rows] for key, rows in data.items()}


def classify_car_status(car, bookings_for_car, today=None):
    today = today or date.today()
    for booking in bookings_for_car:
        start_date = parse_iso_date(booking.get("start"))
        end_date = parse_iso_date(booking.get("end"))
        if not start_date or not end_date:
            continue
        if start_date <= today < end_date:
            if (booking.get("status") or "").lower() == "repair":
                return "service"
            return "rented"

    if not car.get("insurance_valid_until") and not car.get("inspection_valid_until"):
        return "inactive"
    return "available"


def get_service_snapshot(car, service_records, mileage_logs, start_date, end_date):
    latest_service_mileage = find_latest_record(service_records, "mileage")
    next_service_mileage = find_latest_record(service_records, "next_service_mileage")
    mileage_stats = mileage_summary(mileage_logs, start_date, end_date)
    current_mileage = mileage_stats["current_mileage"] or safe_int(car.get("current_mileage"))
    service_interval_km = safe_int(car.get("service_interval_km"))
    baseline_last_service = safe_int(latest_service_mileage) or safe_int(car.get("last_service_mileage"))

    if not next_service_mileage and service_interval_km and baseline_last_service:
        next_service_mileage = baseline_last_service + service_interval_km
    elif not next_service_mileage and service_interval_km and current_mileage:
        next_service_mileage = current_mileage + service_interval_km

    remaining_km = safe_int(next_service_mileage) - current_mileage if next_service_mileage else 0
    is_due = bool(next_service_mileage and current_mileage and remaining_km <= SERVICE_SOON_THRESHOLD_KM)

    return {
        "current_mileage": current_mileage,
        "last_service_mileage": baseline_last_service,
        "next_service_mileage": safe_int(next_service_mileage),
        "service_interval_km": service_interval_km,
        "remaining_km": remaining_km,
        "is_due": is_due,
        "mileage_stats": mileage_stats,
    }


def mileage_summary(logs, start_date, end_date):
    parsed_logs = []
    for log in logs:
        log_date = parse_iso_date(log.get("date"))
        if not log_date:
            continue
        parsed_logs.append(
            {
                **log,
                "parsed_date": log_date,
                "mileage": safe_int(log.get("mileage")),
            }
        )

    parsed_logs.sort(key=lambda item: (item["parsed_date"], item["id"]))
    if not parsed_logs:
        return {
            "distance": 0,
            "current_mileage": 0,
            "chart_points": [],
        }

    baseline = None
    latest_in_range = None
    chart_points = []
    current_mileage = parsed_logs[-1]["mileage"]

    for log in parsed_logs:
        log_date = log["parsed_date"]
        if log_date < start_date:
            baseline = log["mileage"]
        if start_date <= log_date < end_date:
            if baseline is None:
                baseline = log["mileage"]
            latest_in_range = log["mileage"]
            chart_points.append({"x": log_date.isoformat(), "y": log["mileage"]})

    if latest_in_range is None:
        distance = 0
    else:
        distance = max(latest_in_range - (baseline or latest_in_range), 0)

    return {
        "distance": distance,
        "current_mileage": current_mileage,
        "chart_points": chart_points,
    }


def aggregate_booking_metrics(bookings, start_date, end_date):
    daily_revenue = defaultdict(float)
    daily_booking_count = defaultdict(int)
    occupied_days = set()
    service_days = set()
    total_revenue = 0.0
    booking_count = 0
    booking_durations = []

    for booking in bookings:
        booking_start = parse_iso_date(booking.get("start"))
        booking_end = parse_iso_date(booking.get("end"))
        if not booking_start or not booking_end or booking_end <= booking_start:
            continue

        overlap_start, overlap_end = clip_range(booking_start, booking_end, start_date, end_date)
        if not overlap_start:
            continue

        total_days = max((booking_end - booking_start).days, 1)
        overlap_days = max((overlap_end - overlap_start).days, 0)
        if overlap_days <= 0:
            continue

        booking_status = (booking.get("status") or "").lower()
        if booking_status == "repair":
            for day_item in daterange(overlap_start, overlap_end):
                service_days.add(day_item.isoformat())
            continue

        booking_price = safe_float(booking.get("price"))
        allocated_revenue = booking_price * safe_divide(overlap_days, total_days)
        total_revenue += allocated_revenue
        booking_count += 1
        booking_durations.append(total_days)
        daily_booking_count[overlap_start.isoformat()] += 1

        revenue_per_day = safe_divide(booking_price, total_days)
        for day_item in daterange(overlap_start, overlap_end):
            daily_revenue[day_item.isoformat()] += revenue_per_day
            occupied_days.add(day_item.isoformat())

    return {
        "revenue_total": total_revenue,
        "booking_count": booking_count,
        "occupied_days": len(occupied_days),
        "service_days": len(service_days),
        "average_duration": safe_divide(sum(booking_durations), len(booking_durations)),
        "daily_revenue": daily_revenue,
        "daily_booking_count": daily_booking_count,
    }


def normalize_expense_record(record, fallback_category):
    category = (record.get("category") or fallback_category or "").strip().lower()
    return {
        "id": record.get("id"),
        "vehicle_id": record.get("vehicle_id"),
        "date": format_date_or_default(record.get("date"), default="Not set"),
        "raw_date": record.get("date"),
        "amount": format_money(record.get("amount")),
        "notes": record.get("notes") or "",
        "category": category or fallback_category,
        "created_at": record.get("created_at") or "",
    }


def collect_expense_records(records):
    all_records = []
    for record in records.get("vehicle_expenses", []):
        all_records.append(normalize_expense_record(record, record.get("category") or "other"))
    for record in records.get("service_records", []):
        all_records.append(normalize_expense_record(record, record.get("category") or "service"))
    for record in records.get("tire_changes", []):
        all_records.append(normalize_expense_record(record, record.get("category") or "tires"))
    for record in records.get("insurance_records", []):
        all_records.append(normalize_expense_record(record, record.get("category") or "insurance"))
    for record in records.get("fines", []):
        all_records.append(normalize_expense_record(record, record.get("category") or "fines"))
    for record in records.get("inspection_logs", []):
        all_records.append(normalize_expense_record(record, record.get("category") or "inspection"))
    return all_records


def filter_records_by_date(records, start_date, end_date):
    filtered = []
    for record in records:
        record_date = parse_iso_date(record.get("raw_date") or record.get("date"))
        if record_date and start_date <= record_date < end_date:
            filtered.append(record)
    return filtered


def find_latest_record(records, key_name):
    dated_records = []
    for record in records:
        record_date = parse_iso_date(record.get("date"))
        if record_date:
            dated_records.append((record_date, record))
    if not dated_records:
        return None
    dated_records.sort(key=lambda item: item[0])
    return dated_records[-1][1].get(key_name)


def bucketize_series(buckets, per_day_values):
    series = []
    for bucket in buckets:
        total = 0.0
        for day_item in daterange(bucket["start"], bucket["end"]):
            total += safe_float(per_day_values.get(day_item.isoformat()))
        series.append(total)
    return series


def bucketize_counts(buckets, count_values):
    series = []
    for bucket in buckets:
        total = 0
        for day_item in daterange(bucket["start"], bucket["end"]):
            total += safe_int(count_values.get(day_item.isoformat()))
        series.append(total)
    return series


def build_vehicle_gallery(car):
    label = (car.get("name") or "Авто").strip()
    return [
        {
            "title": label,
            "subtitle": car.get("description") or "Автомобиль автопарка",
            "image_url": None,
            "placeholder": True,
        }
    ]


def build_best_month(bookings):
    revenue_by_month = defaultdict(float)
    for booking in bookings:
        booking_start = parse_iso_date(booking.get("start"))
        booking_end = parse_iso_date(booking.get("end"))
        if not booking_start or not booking_end or booking_end <= booking_start:
            continue
        month_key = booking_start.strftime("%Y-%m")
        revenue_by_month[month_key] += safe_float(booking.get("price"))
    if not revenue_by_month:
        return {"label": "Нет данных", "revenue": 0.0}

    best_key = max(revenue_by_month, key=revenue_by_month.get)
    best_date = datetime.strptime(best_key + "-01", "%Y-%m-%d").date()
    return {"label": best_date.strftime("%B %Y"), "revenue": format_money(revenue_by_month[best_key])}


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS cars(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            year INTEGER,
            car_number TEXT,
            vin TEXT,
            passport TEXT,
            insurance_policy_number TEXT,
            insurance_valid_until TEXT,
            inspection_valid_until TEXT,
            service_due_date TEXT,
            description TEXT,
            price_1_3 REAL,
            price_4_7 REAL,
            price_8_14 REAL,
            current_mileage INTEGER,
            last_service_mileage INTEGER,
            service_interval_km INTEGER,
            rental_contract_file TEXT,
            rental_contract_url TEXT,
            insurance_file TEXT,
            inspection_file TEXT,
            plate TEXT,
            insurance TEXT
        )
        """
    )
    ensure_car_schema(c)

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            car_id INTEGER NOT NULL,
            client TEXT,
            booking_time TEXT,
            phone TEXT,
            start TEXT NOT NULL,
            end TEXT NOT NULL,
            price REAL,
            deposit REAL,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'reserved',
            FOREIGN KEY (car_id) REFERENCES cars(id) ON DELETE CASCADE
        )
        """
    )
    ensure_booking_schema(c)

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            ip_address TEXT,
            username TEXT,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT,
            details TEXT
        )
        """
    )
    ensure_audit_log_schema(c)

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'viewer')),
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """
    )

    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bookings_car_dates
        ON bookings(car_id, start, end)
        """
    )
    ensure_analytics_tables(c)

    conn.commit()
    seed_default_users(conn)
    conn.close()
    sync_seed_to_db()


def seed_default_users(conn):
    existing_count = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
    if existing_count:
        return

    admin_password = os.getenv("BOOKING_CARS_ADMIN_PASSWORD", "admin123")
    viewer_password = os.getenv("BOOKING_CARS_VIEWER_PASSWORD", "viewer123")
    conn.executemany(
        """
        INSERT INTO users(username, password_hash, role, is_active)
        VALUES (?, ?, ?, 1)
        """,
        [
            ("admin", generate_password_hash(admin_password), "admin"),
            ("viewer", generate_password_hash(viewer_password), "viewer"),
        ],
    )
    conn.commit()


def add_audit_log(ip_address, username, action, entity_type, entity_id, details):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO audit_logs(created_at, ip_address, username, action, entity_type, entity_id, details)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.utcnow().isoformat(timespec="seconds"),
            ip_address,
            username,
            action,
            entity_type,
            str(entity_id) if entity_id is not None else None,
            json.dumps(details, ensure_ascii=False) if details is not None else None,
        ),
    )
    conn.commit()
    conn.close()


def list_audit_logs(limit=200):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, created_at, ip_address, username, action, entity_type, entity_id, details
        FROM audit_logs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    logs = []
    for row in rows:
        details = row["details"]
        logs.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "ip_address": row["ip_address"] or "-",
                "username": row["username"] or "-",
                "action": row["action"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"] or "-",
                "details": json.loads(details) if details else None,
            }
        )
    return logs


def get_user_by_username(username):
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, username, password_hash, role, is_active
        FROM users
        WHERE username = ?
        """,
        (username,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id):
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, username, password_hash, role, is_active
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def authenticate_user(username, password):
    user = get_user_by_username(username)
    if not user or not user["is_active"]:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


def sync_seed_to_db():
    seed_cars = load_seed_cars()
    conn = get_conn()
    c = conn.cursor()

    for car in seed_cars:
        existing = c.execute("SELECT id FROM cars WHERE name = ?", (car["name"],)).fetchone()
        if not existing:
            for alias in SEED_ALIASES.get(car["name"], []):
                existing = c.execute("SELECT id FROM cars WHERE name = ?", (alias,)).fetchone()
                if existing:
                    break
        payload = (
            car["name"],
            car.get("year"),
            car.get("car_number"),
            car.get("vin"),
            car.get("passport"),
            car.get("insurance_policy_number"),
            car.get("insurance_valid_until"),
            car.get("inspection_valid_until"),
            car.get("service_due_date"),
            car.get("description"),
            car.get("price_1_3"),
            car.get("price_4_7"),
            car.get("price_8_14"),
            car.get("current_mileage"),
            car.get("last_service_mileage"),
            car.get("service_interval_km"),
            car.get("rental_contract_file"),
            car.get("rental_contract_url"),
            car.get("insurance_file"),
            car.get("inspection_file"),
            car.get("car_number"),
            car.get("insurance_policy_number"),
        )

        if existing:
            c.execute(
                """
                UPDATE cars
                SET name = ?, year = ?, car_number = ?, vin = ?, passport = ?,
                    insurance_policy_number = ?, insurance_valid_until = ?, inspection_valid_until = ?, service_due_date = ?,
                    description = ?, price_1_3 = ?, price_4_7 = ?, price_8_14 = ?,
                    current_mileage = ?, last_service_mileage = ?, service_interval_km = ?,
                    rental_contract_file = ?, rental_contract_url = ?, insurance_file = ?, inspection_file = ?, plate = ?, insurance = ?
                WHERE id = ?
                """,
                (*payload, existing["id"]),
            )
        else:
            c.execute(
                """
                INSERT INTO cars
                (name, year, car_number, vin, passport, insurance_policy_number,
                 insurance_valid_until, inspection_valid_until, service_due_date, description, price_1_3, price_4_7, price_8_14,
                 current_mileage, last_service_mileage, service_interval_km,
                 rental_contract_file, rental_contract_url, insurance_file, inspection_file, plate, insurance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )

    conn.commit()
    conn.close()


def list_cars():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, name, year, car_number, vin, passport,
               insurance_policy_number, insurance_valid_until, inspection_valid_until, service_due_date,
               description, price_1_3, price_4_7, price_8_14,
               current_mileage, last_service_mileage, service_interval_km,
               rental_contract_file, rental_contract_url, insurance_file, inspection_file
        FROM cars
        ORDER BY name
        """
    ).fetchall()
    conn.close()
    return [normalize_car_row(row) for row in rows]


def get_car(car_id):
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, name, year, car_number, vin, passport,
               insurance_policy_number, insurance_valid_until, inspection_valid_until, service_due_date,
               description, price_1_3, price_4_7, price_8_14,
               current_mileage, last_service_mileage, service_interval_km,
               rental_contract_file, rental_contract_url, insurance_file, inspection_file
        FROM cars
        WHERE id = ?
        """,
        (car_id,),
    ).fetchone()
    conn.close()

    if not row:
        return None

    return normalize_car_row(row)


def create_car_record(data):
    conn = get_conn()
    duplicate = conn.execute("SELECT id FROM cars WHERE name = ?", (data["name"],)).fetchone()
    if duplicate:
        conn.close()
        return None, "duplicate_name"

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO cars
        (name, year, car_number, vin, passport, insurance_policy_number, insurance_valid_until, inspection_valid_until,
         service_due_date, description, price_1_3, price_4_7, price_8_14, current_mileage, last_service_mileage, service_interval_km,
         rental_contract_file, rental_contract_url, insurance_file, inspection_file, plate, insurance)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["name"],
            data["year"],
            data["car_number"],
            data["vin"],
            data["passport"],
            data["insurance_policy_number"],
            data["insurance_valid_until"],
            data["inspection_valid_until"],
            data["service_due_date"],
            data["description"],
            data["price_1_3"],
            data["price_4_7"],
            data["price_8_14"],
            data["current_mileage"],
            data["last_service_mileage"],
            data["service_interval_km"],
            data["rental_contract_file"],
            data["rental_contract_url"],
            data["insurance_file"],
            data["inspection_file"],
            data["car_number"],
            data["insurance_policy_number"],
        ),
    )
    car_id = cursor.lastrowid
    conn.commit()
    conn.close()

    seed_cars = load_seed_cars()
    seed_cars.append(dict(data))
    save_seed_cars(seed_cars)

    return get_car(car_id), None


def update_car_record(car_id, data):
    conn = get_conn()
    current = conn.execute(
        """
        SELECT id, name
        FROM cars
        WHERE id = ?
        """,
        (car_id,),
    ).fetchone()
    if not current:
        conn.close()
        return None, "car_not_found"

    duplicate = conn.execute(
        "SELECT id FROM cars WHERE name = ? AND id <> ?",
        (data["name"], car_id),
    ).fetchone()
    if duplicate:
        conn.close()
        return None, "duplicate_name"

    conn.execute(
        """
        UPDATE cars
        SET name = ?, year = ?, car_number = ?, vin = ?, passport = ?,
            insurance_policy_number = ?, insurance_valid_until = ?, inspection_valid_until = ?, service_due_date = ?, description = ?,
            price_1_3 = ?, price_4_7 = ?, price_8_14 = ?, current_mileage = ?, last_service_mileage = ?, service_interval_km = ?,
            rental_contract_file = ?, rental_contract_url = ?, insurance_file = ?, inspection_file = ?, plate = ?, insurance = ?
        WHERE id = ?
        """,
        (
            data["name"],
            data["year"],
            data["car_number"],
            data["vin"],
            data["passport"],
            data["insurance_policy_number"],
            data["insurance_valid_until"],
            data["inspection_valid_until"],
            data["service_due_date"],
            data["description"],
            data["price_1_3"],
            data["price_4_7"],
            data["price_8_14"],
            data["current_mileage"],
            data["last_service_mileage"],
            data["service_interval_km"],
            data["rental_contract_file"],
            data["rental_contract_url"],
            data["insurance_file"],
            data["inspection_file"],
            data["car_number"],
            data["insurance_policy_number"],
            car_id,
        ),
    )
    conn.commit()
    conn.close()

    old_name = current["name"]
    seed_cars = load_seed_cars()
    replaced = False
    for index, car in enumerate(seed_cars):
        if car.get("name") == old_name:
            seed_cars[index] = dict(data)
            replaced = True
            break
    if not replaced:
        seed_cars.append(dict(data))
    save_seed_cars(seed_cars)

    return get_car(car_id), None


def delete_car_record(car_id):
    conn = get_conn()
    current = conn.execute("SELECT name FROM cars WHERE id = ?", (car_id,)).fetchone()
    if not current:
        conn.close()
        return False

    conn.execute("DELETE FROM cars WHERE id = ?", (car_id,))
    conn.commit()
    conn.close()

    old_name = current["name"]
    seed_cars = [car for car in load_seed_cars() if car.get("name") != old_name]
    save_seed_cars(seed_cars)
    return True


def get_analytics_reference_data():
    cars = list_cars()
    return {
        "cars": cars,
        "period_options": [
            {"value": "today", "label": "Сегодня"},
            {"value": "week", "label": "Эта неделя"},
            {"value": "month", "label": "Этот месяц"},
            {"value": "custom", "label": "Свой диапазон"},
        ],
    }


def get_fleet_analytics(start_date, end_date, car_ids=None):
    conn = get_conn()
    records = load_analytics_records(conn)
    conn.close()

    cars = [normalize_car_row(row) for row in records["cars"]]
    selected_car_ids = set(car_ids or [])
    if selected_car_ids:
        cars = [car for car in cars if car["id"] in selected_car_ids]
    bookings_by_car = defaultdict(list)
    for booking in records["bookings"]:
        bookings_by_car[booking["car_id"]].append(booking)

    expense_records = collect_expense_records(records)
    expense_records_by_car = defaultdict(list)
    for record in expense_records:
        expense_records_by_car[record["vehicle_id"]].append(record)

    mileage_logs_by_car = defaultdict(list)
    for record in records["mileage_logs"]:
        mileage_logs_by_car[record["vehicle_id"]].append(record)

    service_records_by_car = defaultdict(list)
    for record in records["service_records"]:
        service_records_by_car[record["vehicle_id"]].append(record)

    inspection_logs_by_car = defaultdict(list)
    for record in records["inspection_logs"]:
        inspection_logs_by_car[record["vehicle_id"]].append(record)

    insurance_records_by_car = defaultdict(list)
    for record in records["insurance_records"]:
        insurance_records_by_car[record["vehicle_id"]].append(record)

    buckets = build_time_buckets(start_date, end_date)
    today = date.today()
    period_days = max((end_date - start_date).days, 1)
    fleet_daily_occupied = defaultdict(int)
    fleet_daily_size = defaultdict(int)
    fleet_daily_revenue = defaultdict(float)
    fleet_daily_booking_count = defaultdict(int)
    table_rows = []
    active_cars = 0

    for car in cars:
        car_bookings = bookings_by_car[car["id"]]
        current_status = classify_car_status(car, car_bookings, today=today)
        if current_status != "inactive":
            active_cars += 1

        booking_metrics = aggregate_booking_metrics(car_bookings, start_date, end_date)
        filtered_expenses = filter_records_by_date(expense_records_by_car[car["id"]], start_date, end_date)
        service_snapshot = get_service_snapshot(
            car,
            service_records_by_car[car["id"]],
            mileage_logs_by_car[car["id"]],
            start_date,
            end_date,
        )
        mileage_stats = service_snapshot["mileage_stats"]

        total_expenses = sum(item["amount"] for item in filtered_expenses)
        profit = booking_metrics["revenue_total"] - total_expenses
        utilization_pct = safe_divide(booking_metrics["occupied_days"] * 100, period_days)
        downtime_days = max(period_days - booking_metrics["occupied_days"] - booking_metrics["service_days"], 0)
        current_mileage = service_snapshot["current_mileage"]

        next_service_mileage = service_snapshot["next_service_mileage"]
        last_service_mileage = service_snapshot["last_service_mileage"]
        latest_inspection_due = find_latest_record(inspection_logs_by_car[car["id"]], "due_date")
        latest_insurance_expiry = find_latest_record(insurance_records_by_car[car["id"]], "expiry_date")

        for day_key in booking_metrics["daily_revenue"]:
            fleet_daily_occupied[day_key] += 1
        for day_key, value in booking_metrics["daily_revenue"].items():
            fleet_daily_revenue[day_key] += safe_float(value)
        for day_key, value in booking_metrics["daily_booking_count"].items():
            fleet_daily_booking_count[day_key] += safe_int(value)
        for day_item in daterange(start_date, end_date):
            fleet_daily_size[day_item.isoformat()] += 1

        table_rows.append(
            {
                "id": car["id"],
                "photo_url": None,
                "photo_label": car["name"][:2].upper(),
                "car_name": car["name"],
                "plate_number": car["car_number"] or "-",
                "total_bookings": booking_metrics["booking_count"],
                "revenue": format_money(booking_metrics["revenue_total"]),
                "expenses": format_money(total_expenses),
                "profit": format_money(profit),
                "mileage": mileage_stats["distance"],
                "revenue_per_km": format_money(safe_divide(booking_metrics["revenue_total"], mileage_stats["distance"])),
                "utilization_pct": format_percentage(utilization_pct),
                "downtime_days": downtime_days,
                "inspection_date": format_date_or_default(latest_inspection_due or car.get("inspection_valid_until")),
                "insurance_expiry": format_date_or_default(latest_insurance_expiry or car.get("insurance_valid_until")),
                "last_service_mileage": last_service_mileage,
                "next_service_mileage": next_service_mileage,
                "service_interval_km": service_snapshot["service_interval_km"],
                "service_due": service_snapshot["is_due"],
            }
        )

    total_revenue = sum(row["revenue"] for row in table_rows)
    total_bookings = sum(row["total_bookings"] for row in table_rows)
    total_expenses = sum(row["expenses"] for row in table_rows)
    total_profit = sum(row["profit"] for row in table_rows)
    total_mileage = sum(row["mileage"] for row in table_rows)
    service_soon = 0
    for row in table_rows:
        inspection_days = None
        inspection_date = parse_iso_date(row["inspection_date"])
        if inspection_date:
            inspection_days = (inspection_date - today).days
        if row["service_due"] or (inspection_days is not None and inspection_days <= 30):
            service_soon += 1

    utilization_trend = []
    for bucket in buckets:
        occupied = 0
        size = 0
        for day_item in daterange(bucket["start"], bucket["end"]):
            day_key = day_item.isoformat()
            occupied += fleet_daily_occupied.get(day_key, 0)
            size += fleet_daily_size.get(day_key, 0)
        utilization_trend.append(format_percentage(safe_divide(occupied * 100, size)))

    expense_breakdown = defaultdict(float)
    expense_history = defaultdict(float)
    filtered_all_expenses = [item for row in table_rows for item in filter_records_by_date(expense_records_by_car[row["id"]], start_date, end_date)]
    for expense in filtered_all_expenses:
        if expense["category"] in EXPENSE_BREAKDOWN_CATEGORIES:
            expense_breakdown[expense["category"]] += expense["amount"]
        expense_date = parse_iso_date(expense.get("raw_date"))
        if expense_date:
            expense_history[expense_date.isoformat()] += expense["amount"]

    revenue_top = sorted(table_rows, key=lambda item: item["revenue"], reverse=True)[:10]
    bookings_top = sorted(table_rows, key=lambda item: item["total_bookings"], reverse=True)[:10]
    labels = [bucket["label"] for bucket in buckets]

    fleet_payload = {
        "summary": {
            "total_revenue": format_money(total_revenue),
            "total_bookings": total_bookings,
            "fleet_utilization_pct": format_percentage(safe_divide(sum(row["utilization_pct"] for row in table_rows), len(table_rows)) if table_rows else 0),
            "total_mileage": total_mileage,
            "total_expenses": format_money(total_expenses),
            "net_profit": format_money(total_profit),
            "average_booking_value": format_money(safe_divide(total_revenue, total_bookings)),
            "average_revenue_per_day": format_money(safe_divide(total_revenue, period_days)),
            "active_cars": active_cars,
            "service_soon": service_soon,
        },
        "charts": {
            "revenue_over_time": serialize_chart_dataset(labels, bucketize_series(buckets, fleet_daily_revenue)),
            "booking_count_over_time": serialize_chart_dataset(labels, bucketize_counts(buckets, fleet_daily_booking_count)),
            "fleet_utilization_trend": serialize_chart_dataset(labels, utilization_trend),
            "top_cars_by_revenue": {
                "labels": [item["car_name"] for item in revenue_top],
                "data": [item["revenue"] for item in revenue_top],
                "is_empty": not revenue_top,
                "empty_message": "Нет данных",
            },
            "top_cars_by_bookings": {
                "labels": [item["car_name"] for item in bookings_top],
                "data": [item["total_bookings"] for item in bookings_top],
                "is_empty": not bookings_top,
                "empty_message": "Нет данных",
            },
            "expense_breakdown": {
                "labels": [category.title() for category in EXPENSE_BREAKDOWN_CATEGORIES if expense_breakdown.get(category)],
                "data": [format_money(expense_breakdown[category]) for category in EXPENSE_BREAKDOWN_CATEGORIES if expense_breakdown.get(category)],
                "is_empty": not any(expense_breakdown.get(category) for category in EXPENSE_BREAKDOWN_CATEGORIES),
                "empty_message": "Нет данных по расходам",
            },
        },
        "table": table_rows,
        "meta": {
            "start_date": start_date.isoformat(),
            "end_date": (end_date - timedelta(days=1)).isoformat(),
            "period_days": period_days,
            "selected_car_ids": sorted(selected_car_ids),
            "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        },
    }
    return fleet_payload


def get_car_analytics(car_id, start_date, end_date):
    conn = get_conn()
    records = load_analytics_records(conn)
    conn.close()

    car_row = next((row for row in records["cars"] if row["id"] == car_id), None)
    if not car_row:
        return None

    car = normalize_car_row(car_row)
    car_bookings = [booking for booking in records["bookings"] if booking["car_id"] == car_id]

    buckets = build_time_buckets(start_date, end_date)
    labels = [bucket["label"] for bucket in buckets]

    booking_metrics = aggregate_booking_metrics(car_bookings, start_date, end_date)
    all_expenses = collect_expense_records(records)
    car_expenses = [record for record in all_expenses if record["vehicle_id"] == car_id]
    filtered_expenses = filter_records_by_date(car_expenses, start_date, end_date)
    mileage_logs = [record for record in records["mileage_logs"] if record["vehicle_id"] == car_id]
    service_snapshot = get_service_snapshot(
        car,
        [record for record in records["service_records"] if record["vehicle_id"] == car_id],
        mileage_logs,
        start_date,
        end_date,
    )
    mileage_stats = service_snapshot["mileage_stats"]

    service_history = [
        {
            "date": format_date_or_default(record.get("date")),
            "amount": format_money(record.get("amount")),
            "mileage": safe_int(record.get("mileage")),
            "next_service_mileage": safe_int(record.get("next_service_mileage")),
            "notes": record.get("notes") or "",
            "category": (record.get("category") or "service").strip().lower(),
        }
        for record in records["service_records"]
        if record["vehicle_id"] == car_id
    ]
    tire_history = [
        {
            "date": format_date_or_default(record.get("date")),
            "amount": format_money(record.get("amount")),
            "mileage": safe_int(record.get("mileage")),
            "next_change_mileage": safe_int(record.get("next_change_mileage")),
            "notes": record.get("notes") or "",
            "category": (record.get("category") or "tires").strip().lower(),
        }
        for record in records["tire_changes"]
        if record["vehicle_id"] == car_id
    ]
    insurance_history = [
        {
            "date": format_date_or_default(record.get("date")),
            "amount": format_money(record.get("amount")),
            "expiry_date": format_date_or_default(record.get("expiry_date")),
            "policy_number": record.get("policy_number") or "-",
            "notes": record.get("notes") or "",
            "category": (record.get("category") or "insurance").strip().lower(),
        }
        for record in records["insurance_records"]
        if record["vehicle_id"] == car_id
    ]
    fine_history = [
        {
            "date": format_date_or_default(record.get("date")),
            "amount": format_money(record.get("amount")),
            "reference_number": record.get("reference_number") or "-",
            "status": record.get("status") or "-",
            "notes": record.get("notes") or "",
            "category": (record.get("category") or "fines").strip().lower(),
        }
        for record in records["fines"]
        if record["vehicle_id"] == car_id
    ]

    expense_daily = defaultdict(float)
    for expense in filtered_expenses:
        expense_date = parse_iso_date(expense.get("raw_date"))
        if expense_date:
            expense_daily[expense_date.isoformat()] += expense["amount"]

    total_expenses = sum(item["amount"] for item in filtered_expenses)
    total_revenue = booking_metrics["revenue_total"]
    total_profit = total_revenue - total_expenses
    period_days = max((end_date - start_date).days, 1)
    days_idle = max(period_days - booking_metrics["occupied_days"] - booking_metrics["service_days"], 0)
    best_month = build_best_month(car_bookings)

    return {
        "car": car,
        "gallery": build_vehicle_gallery(car),
        "summary": {
            "total_revenue": format_money(total_revenue),
            "total_expenses": format_money(total_expenses),
            "net_profit": format_money(total_profit),
            "roi_pct": format_percentage(safe_divide(total_profit * 100, total_expenses)),
            "average_booking_duration": round(booking_metrics["average_duration"], 2),
            "average_daily_revenue": format_money(safe_divide(total_revenue, period_days)),
            "days_idle": days_idle,
            "best_performing_month": best_month["label"],
            "best_performing_month_revenue": best_month["revenue"],
            "current_mileage": service_snapshot["current_mileage"],
            "last_service_mileage": service_snapshot["last_service_mileage"],
            "next_service_mileage": service_snapshot["next_service_mileage"],
            "service_interval_km": service_snapshot["service_interval_km"],
            "service_due": service_snapshot["is_due"],
        },
        "charts": {
            "booking_history": serialize_chart_dataset(labels, bucketize_counts(buckets, booking_metrics["daily_booking_count"])),
            "revenue_history": serialize_chart_dataset(labels, bucketize_series(buckets, booking_metrics["daily_revenue"])),
            "mileage_growth": {
                "labels": [point["x"] for point in mileage_stats["chart_points"]],
                "data": [point["y"] for point in mileage_stats["chart_points"]],
                "is_empty": not mileage_stats["chart_points"],
                "empty_message": "Нет данных по пробегу",
            },
            "expense_history": serialize_chart_dataset(labels, bucketize_series(buckets, expense_daily), empty_message="No expense data available"),
        },
        "tables": {
            "expenses": filtered_expenses,
            "service_history": service_history,
            "tire_history": tire_history,
            "insurance_history": insurance_history,
            "fine_history": fine_history,
        },
        "meta": {
            "start_date": start_date.isoformat(),
            "end_date": (end_date - timedelta(days=1)).isoformat(),
            "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        },
    }
