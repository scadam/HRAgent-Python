from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import azure.functions as func

from workday.client import WorkdayClient, WorkdayError


app = func.FunctionApp()


def _json_response(payload: Dict[str, Any], status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
    )


def _get_logger(context: Optional[func.Context]) -> logging.Logger:
    if context and getattr(context, "logger", None):
        return context.logger
    return logging.getLogger("hragent")


def _extract_bearer_token(req: func.HttpRequest) -> Optional[str]:
    auth_header = req.headers.get("Authorization") or req.headers.get("authorization")
    if not auth_header:
        return None
    prefix = "bearer "
    if auth_header.lower().startswith(prefix):
        return auth_header[len(prefix):].strip()
    return None


HandlerFn = Callable[[WorkdayClient, func.HttpRequest, logging.Logger], func.HttpResponse]


def _handle_request(
    req: func.HttpRequest,
    context: func.Context,
    handler: Callable[[WorkdayClient, func.HttpRequest, logging.Logger], Dict[str, Any]],
) -> func.HttpResponse:
    logger = _get_logger(context)
    token = _extract_bearer_token(req)
    if not token:
        return _json_response({"error": "Unauthorized", "message": "Missing or invalid bearer token"}, 401)

    client = WorkdayClient(token, logger)
    try:
        payload = handler(client, req, logger)
        return _json_response(payload)
    except WorkdayError as exc:
        logger.error("Workday error: %s", exc, extra={"status": exc.status_code, "payload": exc.payload})
        status = exc.status_code if exc.status_code and 400 <= exc.status_code < 600 else 502
        return _json_response(
            {
                "success": False,
                "error": "WorkdayError",
                "message": str(exc),
                "details": exc.payload,
            },
            status,
        )
    except ValueError as exc:
        logger.warning("Bad request: %s", exc)
        return _json_response({"success": False, "error": "BadRequest", "message": str(exc)}, 400)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Unhandled error while processing request")
        return _json_response(
            {
                "success": False,
                "error": "InternalServerError",
                "message": "An unexpected error occurred.",
            },
            500,
        )


@app.route(route="getWorker", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_worker(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    def handler(client: WorkdayClient, _req: func.HttpRequest, logger: logging.Logger) -> Dict[str, Any]:
        logger.info("Processing worker profile request")
        worker = client.transform_worker_profile()
        return worker

    return _handle_request(req, context, handler)


@app.route(route="getLeaveBalances", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_leave_balances(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    def handler(client: WorkdayClient, _req: func.HttpRequest, logger: logging.Logger) -> Dict[str, Any]:
        logger.info("Processing leave balances request")
        context_ids = client.get_user_context()
        leave_balances = client.get_leave_balances(context_ids.workday_id)
        eligible_absence_types = client.get_eligible_absence_types(context_ids.workday_id)
        leaves_of_absence = client.get_leaves_of_absence(context_ids.workday_id)
        booked_time_off = client.get_time_off_details(context_ids.workday_id)
        return {
            "success": True,
            "leaveBalances": leave_balances,
            "eligibleAbsenceTypes": eligible_absence_types,
            "leavesOfAbsence": leaves_of_absence,
            "bookedTimeOff": booked_time_off,
        }

    return _handle_request(req, context, handler)


def _date_range(start: date, end: date) -> List[date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _create_days_array(
    start_date: str,
    end_date: str,
    quantity: str,
    unit: str,
    reason: str,
    time_off_type_id: str,
) -> List[Dict[str, Any]]:
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("startDate and endDate must use YYYY-MM-DD format") from exc

    if end < start:
        raise ValueError("endDate cannot be earlier than startDate")

    entries: List[Dict[str, Any]] = []
    for day in _date_range(start, end):
        if unit.lower() == "days":
            daily_quantity = "8"
        else:
            daily_quantity = quantity
        iso_date = day.isoformat()
        entries.append(
            {
                "date": f"{iso_date}T08:00:00.000Z",
                "start": f"{iso_date}T08:00:00.000Z",
                "end": f"{iso_date}T17:00:00.000Z",
                "dailyQuantity": daily_quantity,
                "comment": reason,
                "timeOffType": {"id": time_off_type_id},
            }
        )
    return entries


@app.route(route="bookLeave", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def book_leave(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    def handler(client: WorkdayClient, request: func.HttpRequest, logger: logging.Logger) -> Dict[str, Any]:
        logger.info("Processing book leave request")
        try:
            body = request.get_json()
        except ValueError:
            raise ValueError("Request body must be valid JSON") from None
        start_date = body.get("startDate")
        end_date = body.get("endDate")
        time_off_type_id = body.get("timeOffTypeId")
        quantity = str(body.get("quantity", "8"))
        unit = body.get("unit", "Hours")
        reason = body.get("reason", "Time off request")

        if not start_date or not end_date or not time_off_type_id:
            raise ValueError("startDate, endDate, and timeOffTypeId are required")

        days_array = _create_days_array(start_date, end_date, quantity, unit, reason, time_off_type_id)
        context_ids = client.get_user_context()
        result = client.request_time_off(context_ids.workday_id, days_array)

        booked_days = result.get("days") if isinstance(result, dict) else None
        total_quantity = 0.0
        if booked_days:
            for day in booked_days:
                try:
                    total_quantity += float(day.get("dailyQuantity", 0))
                except (TypeError, ValueError):
                    continue
        else:
            total_quantity = sum(float(day.get("dailyQuantity", 0)) for day in days_array)

        return {
            "success": True,
            "message": "Time off request submitted successfully",
            "bookingDetails": {
                "businessProcess": result.get("businessProcessParameters", {}).get("overallBusinessProcess", {}).get("descriptor"),
                "status": result.get("businessProcessParameters", {}).get("overallStatus"),
                "transactionStatus": result.get("businessProcessParameters", {}).get("transactionStatus", {}).get("descriptor"),
                "daysBooked": len(days_array),
                "totalQuantity": total_quantity,
            },
            "workdayResponse": result,
        }

    return _handle_request(req, context, handler)


@app.route(route="changeBusinessTitle", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def change_business_title(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    def handler(client: WorkdayClient, request: func.HttpRequest, logger: logging.Logger) -> Dict[str, Any]:
        logger.info("Processing change business title request")
        body = request.get_json()
        proposed_title = body.get("proposedBusinessTitle")
        if not proposed_title:
            raise ValueError("proposedBusinessTitle is required")
        context_ids = client.get_user_context()
        result = client.change_business_title(context_ids.workday_id, proposed_title)
        return {
            "success": True,
            "message": "Business title change request submitted successfully",
            "changeDetails": result,
        }

    return _handle_request(req, context, handler)


@app.route(route="getDirectReports", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_direct_reports(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    def handler(client: WorkdayClient, _req: func.HttpRequest, logger: logging.Logger) -> Dict[str, Any]:
        logger.info("Processing direct reports request")
        context_ids = client.get_user_context()
        reports = client.get_direct_reports(context_ids.workday_id)
        return {"success": True, "directReports": reports}

    return _handle_request(req, context, handler)


@app.route(route="getPaySlips", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_pay_slips(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    def handler(client: WorkdayClient, _req: func.HttpRequest, logger: logging.Logger) -> Dict[str, Any]:
        logger.info("Processing pay slips request")
        context_ids = client.get_user_context()
        pay_slips = client.get_pay_slips(context_ids.workday_id)
        return {"success": True, "paySlips": pay_slips}

    return _handle_request(req, context, handler)


@app.route(route="getInboxTasks", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_inbox_tasks(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    def handler(client: WorkdayClient, _req: func.HttpRequest, logger: logging.Logger) -> Dict[str, Any]:
        logger.info("Processing inbox tasks request")
        context_ids = client.get_user_context()
        tasks = client.get_inbox_tasks(context_ids.workday_id)
        return {"success": True, "tasks": tasks}

    return _handle_request(req, context, handler)


@app.route(route="getLearningAssignments", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_learning_assignments(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    def handler(client: WorkdayClient, _req: func.HttpRequest, logger: logging.Logger) -> Dict[str, Any]:
        logger.info("Processing learning assignments request")
        context_ids = client.get_user_context()
        assignments = client.get_learning_assignments(context_ids.workday_id)
        return {"success": True, "assignments": assignments, "total": len(assignments)}

    return _handle_request(req, context, handler)


@app.route(route="getTimeOffEntries", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_time_off_entries(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    def handler(client: WorkdayClient, _req: func.HttpRequest, logger: logging.Logger) -> Dict[str, Any]:
        logger.info("Processing time off entries request")
        context_ids = client.get_user_context()
        entries = client.get_time_off_entries(context_ids.workday_id)
        return {"success": True, "timeOffEntries": entries}

    return _handle_request(req, context, handler)


def _default_leave_dates() -> Tuple[str, str]:
    tomorrow = date.today() + timedelta(days=1)
    iso_date = tomorrow.isoformat()
    return iso_date, iso_date


@app.route(route="requestLeave", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def request_leave(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    def handler(client: WorkdayClient, request: func.HttpRequest, logger: logging.Logger) -> Dict[str, Any]:
        logger.info("Processing request leave preparation")
        try:
            body = request.get_json()
        except ValueError:
            body = {}

        default_start, default_end = _default_leave_dates()
        start_date = body.get("startDate", default_start)
        end_date = body.get("endDate", default_end)
        quantity = str(body.get("quantity", "1"))
        unit = body.get("unit", "Days")
        reason = body.get("reason", "Vacation")

        context_ids = client.get_user_context()
        workday_id = context_ids.workday_id
        eligible_absence_types = client.get_eligible_absence_types(workday_id)
        leave_balances = client.get_leave_balances(workday_id)
        booked_time_off = client.get_time_off_details(workday_id)

        return {
            "success": True,
            "requestParameters": {
                "startDate": start_date,
                "endDate": end_date,
                "quantity": quantity,
                "unit": unit,
                "reason": reason,
            },
            "eligibleAbsenceTypes": eligible_absence_types,
            "leaveBalances": leave_balances,
            "bookedTimeOff": booked_time_off,
            "workdayId": workday_id,
            "bookingGuidance": {
                "timeFormat": "ISO 8601 with timezone (e.g., 2025-02-25T08:00:00.000Z)",
                "defaultWorkingHours": {
                    "start": "08:00:00.000Z",
                    "end": "17:00:00.000Z",
                },
                "quantityCalculation": {
                    "forHours": "Use dailyDefaultQuantity * number of days",
                    "forDays": "Use 1 per day requested",
                },
            },
        }

    return _handle_request(req, context, handler)


def _flatten_lesson(lesson: Dict[str, Any]) -> Dict[str, Any]:
    instructor_led = lesson.get("instructorLedData") or {}
    media_data = lesson.get("mediaData") or {}
    training_activity = lesson.get("trainingActivityData") or {}
    virtual_classroom = instructor_led.get("virtualClassroomData") or {}
    in_person = instructor_led.get("inPersonLedData") or {}
    return {
        "id": lesson.get("id"),
        "descriptor": lesson.get("descriptor"),
        "description": lesson.get("description"),
        "order": lesson.get("order"),
        "required": lesson.get("required"),
        "contentType": lesson.get("contentType", {}).get("descriptor"),
        "duration": instructor_led.get("duration") or media_data.get("duration"),
        "contentURL": lesson.get("externalContentData", {}).get("contentURL"),
        "instructors": [item.get("descriptor") for item in instructor_led.get("instructors", [])],
        "materials": [item.get("descriptor") for item in training_activity.get("materials", [])],
        "activityType": training_activity.get("activityType", {}).get("descriptor"),
        "virtualClassroomURL": virtual_classroom.get("virtualClassroomURL"),
        "location": in_person.get("adhocLocationName"),
        "trackAttendance": instructor_led.get("trackAttendance") or training_activity.get("trackAttendance"),
        "trackGrades": instructor_led.get("trackGrades") or training_activity.get("trackGrades"),
    }


def _flatten_content(content: Dict[str, Any], lessons: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "id": content.get("id"),
        "descriptor": content.get("descriptor"),
        "description": content.get("description"),
        "contentNumber": content.get("contentNumber"),
        "contentURL": content.get("contentURL"),
        "version": content.get("version"),
        "createdOnDate": content.get("createdOnDate"),
        "averageRating": content.get("averageRating"),
        "ratingCount": content.get("ratingCount"),
        "popularity": content.get("popularity"),
        "contentType": content.get("contentType", {}).get("descriptor"),
        "contentProvider": content.get("contentProvider", {}).get("descriptor"),
        "accessType": content.get("accessType", {}).get("descriptor"),
        "deliveryMode": content.get("deliveryMode", {}).get("descriptor"),
        "skillLevel": content.get("skillLevel", {}).get("descriptor"),
        "lifecycleStatus": content.get("lifecycleStatus", {}).get("descriptor"),
        "availabilityStatus": content.get("availabilityStatus", {}).get("descriptor"),
        "excludeFromRecommendations": content.get("excludeFromRecommendations"),
        "excludeFromSearchAndBrowse": content.get("excludeFromSearchAndBrowse"),
        "learningCatalogs": [item.get("descriptor") for item in content.get("learningCatalogs", [])],
        "languages": [item.get("descriptor") for item in content.get("languages", [])],
        "skills": [item.get("descriptor") for item in content.get("skills", [])],
        "topics": [item.get("descriptor") for item in content.get("topics", [])],
        "securityCategories": [item.get("descriptor") for item in content.get("securityCategories", [])],
        "contactPersons": [item.get("descriptor") for item in content.get("contactPersons", [])],
        "imageURL": (content.get("image") or {}).get("publicURL"),
        "lessons": lessons,
    }


@app.route(route="searchLearningContent", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def search_learning_content(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    def handler(client: WorkdayClient, request: func.HttpRequest, logger: logging.Logger) -> Dict[str, Any]:
        logger.info("Processing learning content search request")
        query = urlsplit(request.url).query
        parsed = parse_qs(query, keep_blank_values=False)
        skills = parsed.get("skills", [])
        topics = parsed.get("topics", [])

        content_payload = client.search_learning_content(skills, topics)
        content_items = content_payload.get("data", [])

        enriched_content: List[Dict[str, Any]] = []
        for item in content_items:
            try:
                lessons_raw = client.get_content_lessons(item.get("id"))
            except WorkdayError as lesson_error:
                logger.warning("Failed to fetch lessons for content %s: %s", item.get("id"), lesson_error)
                lessons_raw = []
            lessons = [_flatten_lesson(lesson) for lesson in lessons_raw]
            enriched_content.append(_flatten_content(item, lessons))

        return {"success": True, "content": enriched_content, "total": len(enriched_content)}

    return _handle_request(req, context, handler)
