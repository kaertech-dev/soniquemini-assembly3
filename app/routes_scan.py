from flask import Blueprint, request, jsonify, session, render_template
from app.auth import authenticate_operator
from app.config import Config
from app.scan import process_board_check, finalize_scan, validate_scan

scan_bp = Blueprint("scan", __name__)

# ── Auth ──────────────────────────────────────────────────────────────────────

@scan_bp.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html")

@scan_bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    employee_num = (data.get("employee_num") or "").strip()
    if not employee_num:
        return jsonify({"ok": False, "message": "Employee number is required."}), 400

    operator = authenticate_operator(employee_num)
    
    if not operator:
        return jsonify({
            "ok": False,
            "message": "Unable to sign in. Check your employee number and try again.",
        }), 401

    session["operator_en"] = operator["operator_en"]
    session["employee_num"] = employee_num
    return jsonify({"ok": True, "operator_en": operator["operator_en"]})

@scan_bp.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

# ── Scan UI ───────────────────────────────────────────────────────────────────

@scan_bp.route("/")
def index():
    if "operator_en" not in session:
        from flask import redirect, url_for
        return redirect(url_for("scan.login_page"))
    return render_template("scan.html",
                           operator_en=session["operator_en"],
                           tray_limit=Config.TRAY_LIMIT)

# ── Board serial checks (steps 1 and 2) ──────────────────────────────────────

@scan_bp.route("/api/check-board", methods=["POST"])
def api_check_board():
    if "operator_en" not in session:
        return jsonify({"ok": False, "message": "Not authenticated."}), 401

    data = request.get_json(force=True)
    serial_num = (data.get("serial_num") or "").strip()
    board_type = (data.get("board_type") or "").strip()

    if not serial_num:
        return jsonify({"ok": False, "message": "Serial number is required."}), 400
    if not board_type:
        return jsonify({"ok": False, "message": "Board type is required."}), 400

    result = process_board_check(serial_num, board_type)
    return jsonify(result)

# ── Serial validation (step 3) ───────────────────────────────────────────────

@scan_bp.route("/api/scan", methods=["POST"])
def api_scan():
    if "operator_en" not in session:
        return jsonify({"ok": False, "message": "Not authenticated."}), 401

    data = request.get_json(force=True)
    serial_num = (data.get("serial_num") or "").strip()
    chargingboard = (data.get("chargingboard") or "").strip()
    controlboard = (data.get("controlboard") or "").strip()

    if not serial_num:
        return jsonify({"ok": False, "message": "Serial number is required."}), 400
    if not chargingboard or not controlboard:
        return jsonify({"ok": False, "message": "Both board serials are required."}), 400

    result = validate_scan(
        serial_num,
        chargingboard,
        controlboard,
    )
    ok = result["status"] == "ok"
    return jsonify({"ok": ok, **result})

# ── Pass / Fail decision (step 4) ────────────────────────────────────────────

@scan_bp.route("/api/scan-decision", methods=["POST"])
def api_scan_decision():
    if "operator_en" not in session:
        return jsonify({"ok": False, "message": "Not authenticated."}), 401

    data = request.get_json(force=True)
    serial_num = (data.get("serial_num") or "").strip()
    chargingboard = (data.get("chargingboard") or "").strip()
    controlboard = (data.get("controlboard") or "").strip()
    decision   = (data.get("decision") or "").strip().lower()
    shift      = (data.get("shift") or "").strip()
    remarks    = (data.get("remarks") or "").strip()

    if not serial_num or not chargingboard or not controlboard:
        return jsonify({
            "ok": False,
            "message": "LED-board serial and both board serials are required.",
        }), 400
    if decision not in ("pass", "fail"):
        return jsonify({"ok": False, "message": "Decision must be 'pass' or 'fail'."}), 400
    if decision == "fail" and not remarks:
        return jsonify({"ok": False, "message": "Please enter a fail reason."}), 400

    result = finalize_scan(
        serial_num=serial_num,
        chargingboard=chargingboard,
        controlboard=controlboard,
        operator_en=session["operator_en"],
        shift=shift,
        decision=decision,
        remarks=remarks,
    )

    ok = result["status"] == "ok"
    return jsonify({"ok": ok, **result})