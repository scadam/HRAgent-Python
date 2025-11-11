from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


DEFAULT_BASE_URL = "https://wd2-impl-services1.workday.com"
DEFAULT_TENANT = "microsoft_dpt6"
WORKER_SEARCH_PATH = "svasireddy/COPILOT_CURRENTUSER?format=json"
LEARNING_REPORT_PATH = "svasireddy/Required_Learning"


class WorkdayError(RuntimeError):
    """Raised when Workday returns an unexpected response."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, payload: Optional[Any] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass
class WorkdayUserContext:
    worker_id: str
    workday_id: str


class WorkdayClient:
    """Small helper around Workday APIs that reuses the caller-provided bearer token."""

    def __init__(self, access_token: str, logger: logging.Logger) -> None:
        self._access_token = access_token
        self._logger = logger

        base_url = os.getenv("WORKDAY_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        tenant = os.getenv("WORKDAY_TENANT", DEFAULT_TENANT)

        self._worker_search_url = os.getenv(
            "WORKDAY_WORKER_SEARCH_URL",
            f"{base_url}/ccx/service/customreport2/{tenant}/{WORKER_SEARCH_PATH}"
        )
        self._workers_api_url = os.getenv(
            "WORKDAY_WORKERS_API_URL",
            f"{base_url}/ccx/api/absenceManagement/v1/{tenant}/workers"
        )
        self._absence_api_base = os.getenv(
            "WORKDAY_ABSENCE_API_BASE",
            f"{base_url}/ccx/api/absenceManagement/v1/{tenant}"
        )
        self._common_api_base = os.getenv(
            "WORKDAY_COMMON_API_BASE",
            f"{base_url}/ccx/api/common/v1/{tenant}"
        )
        self._learning_api_base = os.getenv(
            "WORKDAY_LEARNING_API_BASE",
            f"{base_url}/ccx/api/learning/v1/{tenant}"
        )
        self._learning_assignments_report_url = os.getenv(
            "WORKDAY_LEARNING_ASSIGNMENTS_REPORT_URL",
            f"{base_url}/ccx/service/customreport2/{tenant}/{LEARNING_REPORT_PATH}?format=json"
        )

        self._user_context: Optional[WorkdayUserContext] = None
        self._worker_profile_cache: Optional[Dict[str, Any]] = None

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
    ) -> Any:
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_body,
                timeout=float(os.getenv("WORKDAY_HTTP_TIMEOUT", "30")),
            )
        except requests.RequestException as exc:
            raise WorkdayError(f"Failed to reach Workday: {exc!s}") from exc

        self._logger.debug("Workday %s %s -> %s", method, url, response.status_code)

        content_type = response.headers.get("content-type", "")
        data: Any
        if content_type.startswith("application/json") or response.text:
            try:
                data = response.json()
            except ValueError:
                data = response.text
        else:
            data = None

        if not response.ok:
            raise WorkdayError(
                f"Workday request failed ({response.status_code})",
                status_code=response.status_code,
                payload=data,
            )

        return data

    def get_user_context(self) -> WorkdayUserContext:
        if self._user_context is not None:
            return self._user_context

        payload = self._request("GET", self._worker_search_url)
        entries = payload.get("Report_Entry") if isinstance(payload, dict) else None
        if not entries:
            raise WorkdayError("Workday worker search report returned no entries")

        entry = entries[0]
        worker_id = entry.get("Current_User") or entry.get("current_user")
        workday_id = entry.get("workdayID") or entry.get("workdayId")

        if not worker_id or not workday_id:
            raise WorkdayError("Worker search response did not include expected identifiers", payload=entry)

        self._user_context = WorkdayUserContext(worker_id=str(worker_id), workday_id=str(workday_id))
        return self._user_context

    def get_worker_profile(self) -> Dict[str, Any]:
        if self._worker_profile_cache is not None:
            return self._worker_profile_cache

        user_context = self.get_user_context()
        params = {"search": f"'{user_context.worker_id}'"}
        payload = self._request("GET", self._workers_api_url, params=params)

        data = payload.get("data") if isinstance(payload, dict) else None
        if not data:
            raise WorkdayError("Worker search did not return any results", payload=payload)

        self._worker_profile_cache = data[0]
        return self._worker_profile_cache

    def transform_worker_profile(self) -> Dict[str, Any]:
        profile = self.get_worker_profile()
        return {
            "workdayId": profile.get("id"),
            "workerId": profile.get("workerId"),
            "name": profile.get("descriptor"),
            "email": profile.get("person", {}).get("email"),
            "workerType": profile.get("workerType", {}).get("descriptor"),
            "businessTitle": profile.get("primaryJob", {}).get("businessTitle"),
            "location": profile.get("primaryJob", {}).get("location", {}).get("descriptor"),
            "locationId": profile.get("primaryJob", {}).get("location", {}).get("Location_ID"),
            "country": profile.get("primaryJob", {}).get("location", {}).get("country", {}).get("descriptor"),
            "countryCode": profile.get("primaryJob", {}).get("location", {}).get("country", {}).get("ISO_3166-1_Alpha-3_Code"),
            "supervisoryOrganization": profile.get("primaryJob", {}).get("supervisoryOrganization", {}).get("descriptor"),
            "jobType": profile.get("primaryJob", {}).get("jobType", {}).get("descriptor"),
            "jobProfile": profile.get("primaryJob", {}).get("jobProfile", {}).get("descriptor"),
            "primaryJobId": profile.get("primaryJob", {}).get("id"),
            "primaryJobDescriptor": profile.get("primaryJob", {}).get("descriptor"),
        }

    def get_leave_balances(self, workday_id: Optional[str] = None) -> List[Dict[str, Any]]:
        wid = workday_id or self.get_user_context().workday_id
        url = f"{self._absence_api_base}/balances"
        payload = self._request("GET", url, params={"worker": wid})
        items = payload.get("data") if isinstance(payload, dict) else []
        return [
            {
                "planName": item.get("absencePlan", {}).get("descriptor"),
                "planId": item.get("absencePlan", {}).get("id"),
                "balance": item.get("quantity", "0"),
                "unit": item.get("unit", {}).get("descriptor"),
                "effectiveDate": item.get("effectiveDate"),
                "timeOffTypes": item.get("absencePlan", {}).get("timeoffs", ""),
            }
            for item in items
        ]

    def get_eligible_absence_types(self, workday_id: Optional[str] = None) -> List[Dict[str, Any]]:
        wid = workday_id or self.get_user_context().workday_id
        url = f"{self._absence_api_base}/workers/{wid}/eligibleAbsenceTypes"
        payload = self._request("GET", url)
        items = payload.get("data") if isinstance(payload, dict) else []
        return [
            {
                "name": item.get("descriptor"),
                "id": item.get("id"),
                "unit": item.get("unitOfTime", {}).get("descriptor"),
                "category": item.get("category", {}).get("descriptor"),
                "group": item.get("absenceTypeGroup", {}).get("descriptor"),
                "dailyDefaultQuantity": item.get("dailyDefaultQuantity"),
                "startAndEndTimeRequired": item.get("startAndEndTimeRequired", False),
                "calculateQuantityBasedOnTime": item.get("calculateQuantityBasedOnStartAndEndTime", False),
            }
            for item in items
        ]

    def get_leaves_of_absence(self, workday_id: Optional[str] = None) -> List[Dict[str, Any]]:
        wid = workday_id or self.get_user_context().workday_id
        url = f"{self._absence_api_base}/workers/{wid}/leavesOfAbsence"
        payload = self._request("GET", url)
        items = payload.get("data") if isinstance(payload, dict) else []
        return [
            {
                "id": item.get("id"),
                "leaveType": item.get("leaveType", {}).get("descriptor"),
                "status": item.get("status", {}).get("descriptor"),
                "firstDayOfLeave": item.get("firstDayOfLeave"),
                "lastDayOfWork": item.get("lastDayOfWork"),
                "estimatedLastDay": item.get("estimatedLastDayOfLeave"),
                "comment": item.get("latestLeaveComment", ""),
            }
            for item in items
        ]

    def get_time_off_details(self, workday_id: Optional[str] = None) -> List[Dict[str, Any]]:
        wid = workday_id or self.get_user_context().workday_id
        url = f"{self._absence_api_base}/workers/{wid}/timeOffDetails"
        payload = self._request("GET", url)
        items = payload.get("data") if isinstance(payload, dict) else []
        return [
            {
                "date": item.get("date"),
                "timeOffType": item.get("timeOffType", {}).get("descriptor"),
                "quantity": item.get("quantity"),
                "unit": item.get("unit", {}).get("descriptor"),
                "status": item.get("status", {}).get("descriptor"),
                "comment": item.get("comment", ""),
            }
            for item in items
        ]

    def get_time_off_entries(self, workday_id: Optional[str] = None) -> List[Dict[str, Any]]:
        wid = workday_id or self.get_user_context().workday_id
        url = f"{self._common_api_base}/workers/{wid}/timeOffEntries"
        payload = self._request("GET", url)
        items = payload.get("data") if isinstance(payload, dict) else []
        return [
            {
                "employee": item.get("employee", {}).get("descriptor"),
                "timeOffRequestStatus": item.get("timeOffRequest", {}).get("status"),
                "timeOffRequestDescriptor": item.get("timeOffRequest", {}).get("descriptor"),
                "unitOfTime": item.get("unitOfTime", {}).get("descriptor"),
                "timeOffPlan": item.get("timeOff", {}).get("plan", {}).get("descriptor"),
                "timeOffDescriptor": item.get("timeOff", {}).get("descriptor"),
                "date": item.get("date"),
                "units": item.get("units"),
                "descriptor": item.get("descriptor"),
            }
            for item in items
        ]

    def get_inbox_tasks(self, workday_id: Optional[str] = None) -> List[Dict[str, Any]]:
        wid = workday_id or self.get_user_context().workday_id
        url = f"{self._common_api_base}/workers/{wid}/inboxTasks"
        payload = self._request("GET", url)
        items = payload.get("data") if isinstance(payload, dict) else []
        return [
            {
                "assigned": item.get("assigned"),
                "due": item.get("due"),
                "initiator": item.get("initiator", {}).get("descriptor"),
                "status": item.get("status", {}).get("descriptor"),
                "stepType": item.get("stepType", {}).get("descriptor"),
                "subject": item.get("subject", {}).get("descriptor"),
                "overallProcess": item.get("overallProcess", {}).get("descriptor"),
                "descriptor": item.get("descriptor"),
            }
            for item in items
        ]

    def get_direct_reports(self, workday_id: Optional[str] = None) -> List[Dict[str, Any]]:
        wid = workday_id or self.get_user_context().workday_id
        url = f"{self._common_api_base}/workers/{wid}/directReports"
        payload = self._request("GET", url)
        items = payload.get("data") if isinstance(payload, dict) else []
        return [
            {
                "isManager": item.get("isManager"),
                "primaryWorkPhone": item.get("primaryWorkPhone"),
                "primaryWorkEmail": item.get("primaryWorkEmail"),
                "primarySupervisoryOrganization": item.get("primarySupervisoryOrganization", {}).get("descriptor"),
                "businessTitle": item.get("businessTitle"),
                "descriptor": item.get("descriptor"),
            }
            for item in items
        ]

    def get_pay_slips(self, workday_id: Optional[str] = None) -> List[Dict[str, Any]]:
        wid = workday_id or self.get_user_context().workday_id
        url = f"{self._common_api_base}/workers/{wid}/paySlips"
        payload = self._request("GET", url)
        items = payload.get("data") if isinstance(payload, dict) else []
        return [
            {
                "gross": item.get("gross"),
                "status": item.get("status", {}).get("descriptor"),
                "net": item.get("net"),
                "date": item.get("date"),
                "descriptor": item.get("descriptor"),
            }
            for item in items
        ]

    def request_time_off(
        self,
        workday_id: str,
        days: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        url = f"{self._absence_api_base}/workers/{workday_id}/requestTimeOff"
        payload = self._request("POST", url, json_body={"days": days})
        return payload if isinstance(payload, dict) else {"workdayResponse": payload}

    def change_business_title(self, workday_id: str, proposed_title: str) -> Dict[str, Any]:
        url = f"{self._common_api_base}/workers/{workday_id}/businessTitleChanges"
        payload = self._request("POST", f"{url}?type=me", json_body={"proposedBusinessTitle": proposed_title})
        return payload if isinstance(payload, dict) else {"workdayResponse": payload}

    def get_learning_assignments(self, workday_id: Optional[str] = None) -> List[Dict[str, Any]]:
        wid = workday_id or self.get_user_context().workday_id
        url = f"{self._learning_assignments_report_url}&Worker_s__for_Learning_Assignment%21WID={wid}"
        payload = self._request("GET", url)
        entries = payload.get("Report_Entry") if isinstance(payload, dict) else []
        return [
            {
                "assignmentStatus": entry.get("assignmentStatus"),
                "dueDate": entry.get("dueDate"),
                "learningContent": entry.get("learningContent"),
                "overdue": str(entry.get("overdue", "0")) == "1",
                "required": str(entry.get("required", "0")) == "1",
                "workdayId": entry.get("workdayId") or entry.get("workdayID"),
            }
            for entry in entries
        ]

    def search_learning_content(self, skills: List[str], topics: List[str]) -> Dict[str, Any]:
        url = f"{self._learning_api_base}/content"
        params: Dict[str, Any] = {}
        for skill in skills:
            params.setdefault("skills", []).append(skill)
        for topic in topics:
            params.setdefault("topics", []).append(topic)
        payload = self._request("GET", url, params=params or None)
        return payload if isinstance(payload, dict) else {"data": payload}

    def get_content_lessons(self, content_id: str) -> List[Dict[str, Any]]:
        url = f"{self._learning_api_base}/content/{content_id}/lessons"
        payload = self._request("GET", url)
        return payload.get("data") if isinstance(payload, dict) else []
