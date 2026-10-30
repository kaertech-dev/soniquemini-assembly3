from datetime import datetime

from app.db import get_conn, query_all, query_one

BOARD_STATIONS = {
    "chargingboard": {
        "table": "soniquemini.chargingboard_main",
        "stations": ("test",),
    },
}
REQUIRED_STATIONS = (
    "test",
    "lasermarking",
    "vi",
    "assembly1",
    "assembly2",
)
MAX_FAIL_REPETITIONS = 3

def lookup_board(serial_num: str, board_type: str):
    """Look up a chargingboard and its required station results."""
    config = BOARD_STATIONS.get(board_type)
    if config is None:
        return None

    columns = ", ".join(("serial_num", *config["stations"]))
    return query_one(
        f"""
        SELECT {columns}
        FROM {config["table"]}
        WHERE BINARY serial_num = BINARY %s
        LIMIT 1
        """,
        (serial_num,),
    )

def process_board_check(serial_num: str, board_type: str):
    board_type = board_type.strip().lower()
    config = BOARD_STATIONS.get(board_type)
    if config is None:
        return {
            "status": "fail",
            "message": "Choose the chargingboard station.",
            "ok": False,
        }

    record = lookup_board(serial_num.strip(), board_type)
    if record is None:
        return {
            "status": "not_found",
            "message": f"{board_type.title()} serial '{serial_num}' not found.",
            "ok": False,
        }

    failed = [station for station in config["stations"] if record.get(station) != 1]
    if failed:
        return {
            "status": "fail",
            "message": (
                f"{board_type.title()} serial '{serial_num}' has not passed: "
                f"{', '.join(failed).upper()}."
            ),
            "ok": False,
        }

    return {
        "status": "ok",
        "message": f"{board_type.title()} serial '{serial_num}' passed all required stations.",
        "serial_num": record["serial_num"],
        "ok": True,
    }


def lookup_unit(serial_num: str):
    """Check ledboard_main for the serial and its station statuses."""
    return query_one(
        """
        SELECT serial_num, po_num, test, lasermarking, assembly1, assembly2,
               fvi, packaging
        FROM soniquemini.ledboard_main
        WHERE BINARY serial_num = BINARY %s
        LIMIT 1
        """,
        (serial_num,),
    )


def already_assembly3(serial_num: str):
    return query_one(
        """
        SELECT serial_num, po_num, operator_en, shift, date_time, test_rep, status
        FROM soniquemini.ledboard_assembly3
        WHERE serial_num = %s AND status = 1
        LIMIT 1
        """,
        (serial_num,),
    )


def _fail_serials(serial_num: str):
    return tuple(f"{serial_num}_{attempt}" for attempt in range(1, MAX_FAIL_REPETITIONS + 1))


def _failed_attempt_rows(serial_num: str, cursor=None):
    sql = """
        SELECT serial_num
        FROM soniquemini.ledboard_assembly3
        WHERE serial_num IN (%s, %s, %s) AND status = 0
    """
    params = _fail_serials(serial_num)
    if cursor is None:
        return query_all(sql, params)

    cursor.execute(sql + " FOR UPDATE", params)
    return cursor.fetchall()


def _fail_repetition(rows) -> int:
    recorded_repetitions = [
        int(row["serial_num"].rsplit("_", 1)[1])
        for row in rows
    ]
    return max(len(rows), max(recorded_repetitions, default=0))


def fail_repetition(serial_num: str) -> int:
    """Return the number of failed repetitions already recorded for a serial."""
    return _fail_repetition(_failed_attempt_rows(serial_num))


def stations_passed(row: dict) -> bool:
    """Return True if all required LED-board stations are recorded as passed."""
    return all(row.get(station) == 1 for station in REQUIRED_STATIONS)


def _already_assembly3_message(record: dict) -> str:
    recorded_at = record["date_time"]
    if isinstance(recorded_at, datetime):
        recorded_at = recorded_at.strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"Serial '{record['serial_num']}' already has a saved PASS row "
        f"(PO {record['po_num']}, operator {record['operator_en']}, "
        f"shift {record['shift']}, recorded at {recorded_at}). "
        "No duplicate was written."
    )


def record_assembly3(
    serial_num: str,
    chargingboard: str,
    po_num: str,
    operator_en: str,
    shift: str,
):
    """Atomically prevent duplicate PASS rows and record an assembly3 PASS."""
    conn = get_conn(read_timeout=15)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT serial_num
            FROM soniquemini.ledboard_main
            WHERE BINARY serial_num = BINARY %s
            FOR UPDATE
            """,
            (serial_num,),
        )
        cur.execute(
            """
            SELECT serial_num, po_num, operator_en, shift, date_time, test_rep, status
            FROM soniquemini.ledboard_assembly3
            WHERE serial_num = %s AND status = 1
            LIMIT 1
            FOR UPDATE
            """,
            (serial_num,),
        )
        existing_record = cur.fetchone()
        if existing_record:
            conn.commit()
            return existing_record

        cur.execute(
            """
            UPDATE soniquemini.ledboard_main
            SET assembly3 = 1
            WHERE BINARY serial_num = BINARY %s
            """,
            (serial_num,),
        )
        cur.execute(
            """
            INSERT INTO soniquemini.ledboard_assembly3
                (serial_num, po_num, operator_en, shift, chargingboard, controlboard,
                 date_time, test_rep, remarks, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 1, "", 1)
            """,
            (
                serial_num,
                po_num,
                operator_en,
                shift,
                chargingboard,
                "",
                datetime.now(),
            ),
        )
        conn.commit()
        return None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_fail(
    serial_num: str,
    chargingboard: str,
    po_num: str,
    operator_en: str,
    shift: str,
    reason: str,
):
    """Record a FAIL using a suffixed serial, without changing LED-board stations."""
    conn = get_conn(read_timeout=15)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT serial_num
            FROM soniquemini.ledboard_main
            WHERE BINARY serial_num = BINARY %s
            FOR UPDATE
            """,
            (serial_num,),
        )
        rows = _failed_attempt_rows(serial_num, cur)
        repetition = _fail_repetition(rows) + 1
        if repetition > MAX_FAIL_REPETITIONS:
            conn.rollback()
            return None

        cur.execute(
            """
            INSERT INTO soniquemini.ledboard_assembly3
                (serial_num, po_num, operator_en, shift, chargingboard, controlboard,
                 date_time, test_rep, remarks, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
            """,
            (
                f"{serial_num}_{repetition}",
                po_num,
                operator_en,
                shift,
                chargingboard,
                "",
                datetime.now(),
                repetition,
                reason,
            ),
        )
        conn.commit()
        return repetition
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate_scan(serial_num: str, chargingboard: str):
    """Validate the scanned chargingboard and required LED-board stations."""
    serial_num = serial_num.strip()
    chargingboard = chargingboard.strip()

    if not chargingboard:
        return {
            "status": "fail",
            "message": "Scan and verify the chargingboard serial first.",
            "unit": None,
        }

    result = process_board_check(chargingboard, "chargingboard")
    if not result["ok"]:
        return {"status": result["status"], "message": result["message"], "unit": None}

    unit = lookup_unit(serial_num)
    if not unit:
        return {
            "status": "not_found",
            "message": f"LED-board serial '{serial_num}' not found in database.",
            "unit": None,
        }

    assembly3_record = already_assembly3(serial_num)
    if assembly3_record:
        return {
            "status": "already_assembly3",
            "message": _already_assembly3_message(assembly3_record),
            "unit": unit,
            "saved_record": assembly3_record,
        }

    if fail_repetition(serial_num) >= MAX_FAIL_REPETITIONS:
        return {
            "status": "max_failures",
            "message": (
                f"Serial '{serial_num}' has reached the maximum of "
                f"{MAX_FAIL_REPETITIONS} FAIL attempts."
            ),
            "unit": unit,
        }

    if not stations_passed(unit):
        failed = [station for station in REQUIRED_STATIONS if unit.get(station) != 1]
        return {
            "status": "fail",
            "message": f"LED-board serial '{serial_num}' has not passed: {', '.join(failed).upper()}.",
            "unit": unit,
        }

    return {
        "status": "ok",
        "message": f"LED-board serial '{serial_num}' passed all required stations. Choose PASS or FAIL.",
        "unit": unit,
    }


def finalize_scan(
    serial_num: str,
    chargingboard: str,
    operator_en: str,
    shift: str,
    decision: str,
    remarks: str = "",
):
    """Revalidate the scanned boards and LED-board before saving the decision."""
    serial_num = serial_num.strip()
    chargingboard = chargingboard.strip()
    decision = (decision or "").strip().lower()

    if decision not in ("pass", "fail"):
        return {"status": "fail", "message": "Invalid decision.", "unit": None}

    check = validate_scan(serial_num, chargingboard)
    if check["status"] != "ok":
        if decision == "pass" and check["status"] == "already_assembly3":
            return {
                "status": "ok",
                "message": check["message"],
                "unit": check["unit"],
                "decision": "pass",
                "serial_num": serial_num,
                "already_recorded": True,
            }
        return check

    unit = check["unit"]
    if decision == "fail":
        reason = remarks.strip()
        if not reason:
            return {"status": "fail", "message": "A fail reason is required.", "unit": unit}
        repetition = record_fail(
            serial_num,
            chargingboard,
            unit["po_num"],
            operator_en,
            shift,
            reason,
        )
        if repetition is None:
            return {
                "status": "max_failures",
                "message": (
                    f"Serial '{serial_num}' has reached the maximum of "
                    f"{MAX_FAIL_REPETITIONS} FAIL attempts."
                ),
                "unit": unit,
            }
        return {
            "status": "ok",
            "message": f"Serial '{serial_num}' recorded as FAIL (attempt {repetition} of {MAX_FAIL_REPETITIONS}): {reason}",
            "unit": unit,
            "decision": "fail",
            "serial_num": f"{serial_num}_{repetition}",
        }

    existing_record = record_assembly3(
        serial_num,
        chargingboard,
        unit["po_num"],
        operator_en,
        shift,
    )
    if existing_record:
        return {
            "status": "ok",
            "message": _already_assembly3_message(existing_record),
            "unit": unit,
            "decision": "pass",
            "serial_num": serial_num,
            "already_recorded": True,
        }

    return {
        "status": "ok",
        "message": f"Serial '{serial_num}' recorded as assembly3 successfully.",
        "unit": unit,
        "decision": "pass",
        "serial_num": serial_num,
    }
