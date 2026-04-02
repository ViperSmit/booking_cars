import os
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from datetime import date, timedelta
from werkzeug.middleware.proxy_fix import ProxyFix

from database import (
    add_audit_log,
    authenticate_user,
    create_car_record,
    delete_car_record,
    get_analytics_reference_data,
    get_car_analytics,
    get_car,
    get_conn,
    get_fleet_analytics,
    get_user_by_id,
    init_db,
    list_audit_logs,
    list_cars,
    update_car_record,
)

def load_local_env():
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_local_env()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("BOOKING_CARS_SECRET_KEY", "booking-cars-dev-secret")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("BOOKING_CARS_SESSION_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = os.getenv("BOOKING_CARS_SESSION_SECURE", "false").lower() == "true"
app.config["PERMANENT_SESSION_LIFETIME"] = int(os.getenv("BOOKING_CARS_SESSION_LIFETIME_SECONDS", "43200"))

if os.getenv("BOOKING_CARS_PROXY_FIX", "false").lower() == "true":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

ALLOWED_STATUSES = {"reserved", "issued", "repair"}


def get_request_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def current_username():
    user = getattr(g, "current_user", None)
    return user["username"] if user else "anonymous"


def wants_json_response():
    return request.path.startswith(("/cars", "/car/", "/add", "/update/", "/delete/", "/bookings", "/api/analytics"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.current_user:
            if wants_json_response():
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(required_role):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.current_user:
                if wants_json_response():
                    return jsonify({"error": "unauthorized"}), 401
                return redirect(url_for("login", next=request.path))
            if g.current_user["role"] != required_role:
                if wants_json_response():
                    return jsonify({"error": "forbidden"}), 403
                return redirect(url_for("index"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def validate_booking_payload(data, require_schedule=True):
    if not isinstance(data, dict):
        return None, ("invalid_json", 400)

    car_id = data.get("car_id")
    start = data.get("start")
    end = data.get("end")
    status = data.get("status", "reserved")
    booking_time = (data.get("booking_time") or "").strip()

    if require_schedule:
        if car_id is None or not start or not end:
            return None, ("missing_fields", 400)
        if start >= end:
            return None, ("invalid_range", 400)

    if status not in ALLOWED_STATUSES:
        return None, ("invalid_status", 400)

    if booking_time:
        parts = booking_time.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return None, ("invalid_booking_time", 400)
        hours, minutes = map(int, parts)
        if hours not in range(24) or minutes not in range(60):
            return None, ("invalid_booking_time", 400)

    normalized = dict(data)
    normalized["status"] = status
    normalized["booking_time"] = booking_time or None

    for field in ("price", "deposit"):
        value = normalized.get(field)
        if value in (None, ""):
            normalized[field] = 100.0 if field == "deposit" else None
            continue
        try:
            normalized[field] = float(value)
        except (TypeError, ValueError):
            return None, (f"invalid_{field}", 400)

    return normalized, None


def validate_car_payload(data):
    if not isinstance(data, dict):
        return None, ("invalid_json", 400)

    name = (data.get("name") or "").strip()
    if not name:
        return None, ("missing_name", 400)

    year = data.get("year")
    if year in (None, ""):
        normalized_year = None
    else:
        try:
            normalized_year = int(year)
        except (TypeError, ValueError):
            return None, ("invalid_year", 400)
        if normalized_year < 1900 or normalized_year > 2100:
            return None, ("invalid_year", 400)

    insurance_valid_until = (data.get("insurance_valid_until") or "").strip()
    if insurance_valid_until:
        try:
            date.fromisoformat(insurance_valid_until)
        except ValueError:
            return None, ("invalid_insurance_valid_until", 400)
    else:
        insurance_valid_until = None

    inspection_valid_until = (data.get("inspection_valid_until") or "").strip()
    if inspection_valid_until:
        try:
            date.fromisoformat(inspection_valid_until)
        except ValueError:
            return None, ("invalid_inspection_valid_until", 400)
    else:
        inspection_valid_until = None

    service_due_date = (data.get("service_due_date") or "").strip()
    if service_due_date:
        try:
            date.fromisoformat(service_due_date)
        except ValueError:
            return None, ("invalid_service_due_date", 400)
    else:
        service_due_date = None

    prices = {}
    for field in ("price_1_3", "price_4_7", "price_8_14"):
        value = data.get(field)
        if value in (None, ""):
            prices[field] = None
            continue
        try:
            prices[field] = float(value)
        except (TypeError, ValueError):
            return None, (f"invalid_{field}", 400)
        if prices[field] < 0:
            return None, (f"invalid_{field}", 400)

    integer_fields = {}
    for field in ("current_mileage", "last_service_mileage", "service_interval_km"):
        value = data.get(field)
        if value in (None, ""):
            integer_fields[field] = 0
            continue
        try:
            integer_fields[field] = int(value)
        except (TypeError, ValueError):
            return None, (f"invalid_{field}", 400)
        if integer_fields[field] < 0:
            return None, (f"invalid_{field}", 400)

    return {
        "name": name,
        "year": normalized_year,
        "car_number": (data.get("car_number") or "").strip() or None,
        "vin": (data.get("vin") or "").strip() or None,
        "passport": (data.get("passport") or "").strip() or None,
        "insurance_policy_number": (data.get("insurance_policy_number") or "").strip() or None,
        "insurance_valid_until": insurance_valid_until,
        "inspection_valid_until": inspection_valid_until,
        "service_due_date": service_due_date,
        "description": (data.get("description") or "").strip() or None,
        **integer_fields,
        **prices,
    }, None


def next_month_start(input_date):
    if input_date.month == 12:
        return input_date.replace(year=input_date.year + 1, month=1, day=1)
    return input_date.replace(month=input_date.month + 1, day=1)


def parse_analytics_filters(args):
    today = date.today()
    period = (args.get("period") or "month").strip().lower()
    allowed_periods = {"today", "week", "month", "custom"}

    if period not in allowed_periods:
        period = "month"

    start_date = None
    end_date = None

    if period == "today":
        start_date = today
        end_date = today + timedelta(days=1)
    elif period == "week":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
    elif period == "month":
        start_date = today.replace(day=1)
        end_date = next_month_start(start_date)
    else:
        try:
            start_date = date.fromisoformat((args.get("start_date") or "").strip())
            inclusive_end = date.fromisoformat((args.get("end_date") or "").strip())
            if inclusive_end < start_date:
                start_date, inclusive_end = inclusive_end, start_date
            end_date = inclusive_end + timedelta(days=1)
        except ValueError:
            period = "month"
            start_date = today.replace(day=1)
            end_date = next_month_start(start_date)

    raw_car_ids = (args.get("car_ids") or "").strip()
    selected_car_ids = []
    if raw_car_ids:
        for value in raw_car_ids.split(","):
            value = value.strip()
            if value.isdigit():
                selected_car_ids.append(int(value))

    return {
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "display_start": start_date.isoformat(),
        "display_end": (end_date - timedelta(days=1)).isoformat(),
        "selected_car_id": args.get("car_id", type=int),
        "selected_car_ids": selected_car_ids,
    }


init_db()


@app.before_request
def load_current_user():
    user_id = session.get("user_id")
    g.current_user = get_user_by_id(user_id) if user_id else None


@app.context_processor
def inject_auth_state():
    user = g.get("current_user")
    return {
        "current_user": user,
        "is_admin": bool(user and user["role"] == "admin"),
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.current_user:
        return redirect(url_for("index"))

    error = None
    next_url = request.args.get("next") or request.form.get("next") or url_for("index")

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = authenticate_user(username, password)
        if not user:
            error = "Неверный логин или пароль"
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(next_url)

    return render_template("login.html", error=error, next_url=next_url)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/insurance")
@login_required
def insurance_page():
    return render_template("insurance.html", cars=list_cars())


@app.route("/analytics")
@login_required
def analytics_page():
    reference = get_analytics_reference_data()
    filters = parse_analytics_filters(request.args)
    return render_template("analytics.html", reference=reference, filters=filters)


@app.route("/analytics/car/<int:car_id>")
@login_required
def analytics_car_page(car_id):
    reference = get_analytics_reference_data()
    car = get_car(car_id)
    if not car:
        return "Car not found", 404
    filters = parse_analytics_filters(request.args)
    return render_template("analytics_car.html", reference=reference, filters=filters, car=car)


@app.route("/api/analytics")
@login_required
def analytics_api():
    filters = parse_analytics_filters(request.args)
    payload = get_fleet_analytics(filters["start_date"], filters["end_date"], car_ids=filters["selected_car_ids"])
    payload["filters"] = {
        "period": filters["period"],
        "start_date": filters["display_start"],
        "end_date": filters["display_end"],
        "car_ids": filters["selected_car_ids"],
    }
    return jsonify(payload)


@app.route("/api/analytics/car/<int:car_id>")
@login_required
def analytics_car_api(car_id):
    filters = parse_analytics_filters(request.args)
    payload = get_car_analytics(car_id, filters["start_date"], filters["end_date"])
    if not payload:
        return jsonify({"error": "car_not_found"}), 404
    payload["filters"] = {
        "period": filters["period"],
        "start_date": filters["display_start"],
        "end_date": filters["display_end"],
    }
    return jsonify(payload)


@app.route("/logs")
@role_required("admin")
def logs_page():
    return render_template("logs.html", logs=list_audit_logs())


@app.route("/cars")
@login_required
def cars():
    return jsonify(list_cars())


@app.route("/cars/add", methods=["POST"])
@role_required("admin")
def add_car():
    data, error = validate_car_payload(request.get_json(silent=True))
    if error:
        message, code = error
        return jsonify({"error": message}), code

    car, db_error = create_car_record(data)
    if db_error == "duplicate_name":
        return jsonify({"error": db_error}), 409

    add_audit_log(get_request_ip(), current_username(), "create", "car", car["id"], car)

    return jsonify({"status": "ok", "car": car}), 201


@app.route("/bookings")
@login_required
def bookings():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, car_id, client, booking_time, phone, start, end, price, deposit, note, status
        FROM bookings
        """
    ).fetchall()
    conn.close()

    return jsonify(
        [
            {
                "id": row["id"],
                "resource": row["car_id"],
                "client_name": row["client"],
                "text": row["client"],
                "booking_time": row["booking_time"],
                "display_text": (
                    f"{row['booking_time']} {row['client']}".strip()
                    if row["booking_time"] or row["client"]
                    else ""
                ),
                "phone": row["phone"],
                "start": row["start"],
                "end": row["end"],
                "price": row["price"],
                "deposit": row["deposit"],
                "note": row["note"],
                "status": row["status"] or "reserved",
            }
            for row in rows
        ]
    )


@app.route("/car/<int:car_id>")
@login_required
def car_page(car_id):
    car = get_car(car_id)
    if not car:
        return "Car not found", 404

    return render_template("car.html", car=car)


@app.route("/car/<int:car_id>/update", methods=["POST"])
@role_required("admin")
def update_car(car_id):
    data, error = validate_car_payload(request.get_json(silent=True))
    if error:
        message, code = error
        return jsonify({"error": message}), code

    car, db_error = update_car_record(car_id, data)
    if db_error == "car_not_found":
        return jsonify({"error": db_error}), 404
    if db_error == "duplicate_name":
        return jsonify({"error": db_error}), 409

    add_audit_log(get_request_ip(), current_username(), "update", "car", car_id, car)

    return jsonify({"status": "updated", "id": car_id, "car": car})


@app.route("/car/<int:car_id>/delete", methods=["POST"])
@role_required("admin")
def delete_car(car_id):
    car = get_car(car_id)
    deleted = delete_car_record(car_id)
    if not deleted:
        return jsonify({"error": "car_not_found"}), 404

    add_audit_log(get_request_ip(), current_username(), "delete", "car", car_id, car)

    return jsonify({"status": "deleted", "id": car_id})


@app.route("/add", methods=["POST"])
@role_required("admin")
def add():
    data, error = validate_booking_payload(request.get_json(silent=True), require_schedule=True)
    if error:
        message, code = error
        return jsonify({"error": message}), code

    conn = get_conn()
    car_exists = conn.execute("SELECT 1 FROM cars WHERE id = ?", (data["car_id"],)).fetchone()
    if not car_exists:
        conn.close()
        return jsonify({"error": "car_not_found"}), 404

    conflict = conn.execute(
        """
        SELECT id FROM bookings
        WHERE car_id = ?
        AND NOT (end <= ? OR start >= ?)
        """,
        (data["car_id"], data["start"], data["end"]),
    ).fetchone()
    if conflict:
        conn.close()
        return jsonify({"error": "conflict"}), 409

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bookings
        (car_id, client, booking_time, phone, start, end, price, deposit, note, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["car_id"],
            data.get("client"),
            data.get("booking_time"),
            data.get("phone"),
            data["start"],
            data["end"],
            data.get("price"),
            data.get("deposit"),
            data.get("note"),
            data["status"],
        ),
    )
    booking_id = cursor.lastrowid
    conn.commit()
    conn.close()

    add_audit_log(
        get_request_ip(),
        current_username(),
        "create",
        "booking",
        booking_id,
        {
            "car_id": data["car_id"],
            "client": data.get("client"),
            "booking_time": data.get("booking_time"),
            "start": data["start"],
            "end": data["end"],
            "price": data.get("price"),
            "deposit": data.get("deposit"),
            "status": data["status"],
        },
    )

    return jsonify({"status": "ok", "id": booking_id}), 201


@app.route("/update/<int:booking_id>", methods=["POST"])
@role_required("admin")
def update(booking_id):
    payload = request.get_json(silent=True)

    conn = get_conn()
    existing = conn.execute(
        """
        SELECT car_id, client, booking_time, phone, start, end, price, deposit, note, status
        FROM bookings
        WHERE id = ?
        """,
        (booking_id,),
    ).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "booking_not_found"}), 404

    if isinstance(payload, dict):
        payload = {
            "car_id": payload.get("car_id", existing["car_id"]),
            "start": payload.get("start", existing["start"]),
            "end": payload.get("end", existing["end"]),
            "client": payload.get("client", existing["client"]),
            "booking_time": payload.get("booking_time", existing["booking_time"]),
            "phone": payload.get("phone", existing["phone"]),
            "price": payload.get("price", existing["price"]),
            "deposit": payload.get("deposit", existing["deposit"]),
            "note": payload.get("note", existing["note"]),
            "status": payload.get("status", existing["status"]),
            **payload,
        }

    data, error = validate_booking_payload(payload, require_schedule=True)
    if error:
        message, code = error
        conn.close()
        return jsonify({"error": message}), code

    car_exists = conn.execute("SELECT 1 FROM cars WHERE id = ?", (data["car_id"],)).fetchone()
    if not car_exists:
        conn.close()
        return jsonify({"error": "car_not_found"}), 404

    conflict = conn.execute(
        """
        SELECT id FROM bookings
        WHERE car_id = ?
        AND id <> ?
        AND NOT (end <= ? OR start >= ?)
        """,
        (data["car_id"], booking_id, data["start"], data["end"]),
    ).fetchone()
    if conflict:
        conn.close()
        return jsonify({"error": "conflict"}), 409

    conn.execute(
        """
        UPDATE bookings
        SET car_id = ?, client = ?, booking_time = ?, phone = ?, start = ?, end = ?, price = ?, deposit = ?, note = ?, status = ?
        WHERE id = ?
        """,
        (
            data["car_id"],
            data.get("client"),
            data.get("booking_time"),
            data.get("phone"),
            data["start"],
            data["end"],
            data.get("price"),
            data.get("deposit"),
            data.get("note"),
            data["status"],
            booking_id,
        ),
    )
    conn.commit()
    conn.close()

    add_audit_log(
        get_request_ip(),
        current_username(),
        "update",
        "booking",
        booking_id,
        {
            "car_id": data["car_id"],
            "client": data.get("client"),
            "booking_time": data.get("booking_time"),
            "phone": data.get("phone"),
            "start": data["start"],
            "end": data["end"],
            "price": data.get("price"),
            "deposit": data.get("deposit"),
            "note": data.get("note"),
            "status": data["status"],
        },
    )

    return jsonify({"status": "updated", "id": booking_id})


@app.route("/delete/<int:booking_id>", methods=["POST"])
@role_required("admin")
def delete(booking_id):
    conn = get_conn()
    booking = conn.execute(
        """
        SELECT car_id, client, booking_time, phone, start, end, price, deposit, note, status
        FROM bookings
        WHERE id = ?
        """,
        (booking_id,),
    ).fetchone()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted == 0:
        return jsonify({"error": "booking_not_found"}), 404

    add_audit_log(
        get_request_ip(),
        current_username(),
        "delete",
        "booking",
        booking_id,
        dict(booking) if booking else None,
    )

    return jsonify({"status": "deleted"})


if __name__ == "__main__":
    app.run(
        host=os.getenv("BOOKING_CARS_HOST", "0.0.0.0"),
        port=int(os.getenv("BOOKING_CARS_PORT", "5000")),
        debug=os.getenv("BOOKING_CARS_DEBUG", "false").lower() == "true",
    )
