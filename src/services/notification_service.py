from functools import lru_cache
from pathlib import Path
from datetime import datetime

import httpx

from ..core.configs import settings
from ..core.logger import logger

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates" / "emails"


@lru_cache(maxsize=None)
def _load_template(filename: str) -> str:
    """Load and cache email template from disk. Cached after first read."""
    template_path = TEMPLATE_DIR / filename
    return template_path.read_text(encoding="utf-8")


class NotificationService:
    """Handles sending email notifications via the notification API."""

    SEND_ENDPOINT = "/v2/notification/send"

    def __init__(self):
        self.base_url = settings.NOTIFICATION_BASE_URL
        self.timeout = 30.0

    async def _send(self, payload: dict) -> dict:
        url = f"{self.base_url}{self.SEND_ENDPOINT}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()

    async def send_designation_approved_email(
        self,
        to_email: str,
        designation_name: str,
        approver_name: str,
        approval_date: str,
        user_id: str,
    ) -> None:
        """Send email notification when a designation request is approved."""
        if not settings.ENABLE_EMAIL_NOTIFICATION:
            logger.info("Email notifications disabled – skipping designation approved email")
            return

        template_data = _load_template("designation_approval_request_email copy.html")

        params = {
            "designationName": designation_name,
            "approverName": approver_name,
            "approvalDate": approval_date,
        }

        payload = {
            "request": {
                "notifications": [
                    {
                        "type": "email",
                        "priority": 1,
                        "ids": [to_email],
                        "bccIds": [],
                        "action": {
                            "type": "email",
                            "category": "email",
                            "createdBy": {
                                "id": user_id,
                                "type": "user",
                            },
                            "template": {
                                "data": template_data,
                                "id": "designation-approved",
                                "params": params,
                                "type": "email",
                                "config": {
                                    "subject": "Your Designation Request Has Been Approved",
                                    "sender": "",
                                },
                            },
                        },
                    }
                ]
            }
        }

        try:
            result = await self._send(payload)
            logger.info(f"Designation approved email sent to {to_email}: {result}")
        except Exception as exc:
            logger.error(f"Failed to send designation approved email to {to_email}: {exc}")

    async def send_designation_rejected_email(
        self,
        to_email: str,
        designation_name: str,
        rejector_name: str,
        rejection_date: str,
        rejection_reason: str,
        user_id: str,
    ) -> None:
        """Send email notification when a designation request is rejected."""
        if not settings.ENABLE_EMAIL_NOTIFICATION:
            logger.info("Email notifications disabled – skipping designation rejected email")
            return

        template_data = _load_template("designation_rejection_request_email.html")

        params = {
            "designationName": designation_name,
            "rejectorName": rejector_name,
            "rejectionDate": rejection_date,
            "rejectionReason": rejection_reason if rejection_reason else "N/A",
        }

        payload = {
            "request": {
                "notifications": [
                    {
                        "type": "email",
                        "priority": 1,
                        "ids": [to_email],
                        "bccIds": [],
                        "action": {
                            "type": "email",
                            "category": "email",
                            "createdBy": {
                                "id": user_id,
                                "type": "user",
                            },
                            "template": {
                                "data": template_data,
                                "id": "designation-rejected",
                                "params": params,
                                "type": "email",
                                "config": {
                                    "subject": "Update on Your Designation Request",
                                    "sender": "",
                                },
                            },
                        },
                    }
                ]
            }
        }

        try:
            result = await self._send(payload)
            logger.info(f"Designation rejected email sent to {to_email}: {result}")
        except Exception as exc:
            logger.error(f"Failed to send designation rejected email to {to_email}: {exc}")

    async def send_cbplan_status_email(
        self,
        to_email: str,
        cbp_name: str,
        status: str,
        approver_name: str,
        action_date: str,
        rejection_reason: str,
        user_id: str,
    ) -> None:
        """Send email notification for CBP plan request status (approved/rejected)."""
        if not settings.ENABLE_EMAIL_NOTIFICATION:
            logger.info("Email notifications disabled – skipping CBP plan status email")
            return

        template_data = _load_template("cbplan_request_status_email.html")

        params = {
            "cbpName": cbp_name,
            "status": status,
            "approverName": approver_name,
            "actionDate": action_date,
            "rejectionReason": rejection_reason if rejection_reason else "N/A",
        }

        subject = f'Update on Your AI CBP Request "{cbp_name}"'

        payload = {
            "request": {
                "notifications": [
                    {
                        "type": "email",
                        "priority": 1,
                        "ids": [to_email],
                        "bccIds": [],
                        "action": {
                            "type": "email",
                            "category": "email",
                            "createdBy": {
                                "id": user_id,
                                "type": "user",
                            },
                            "template": {
                                "data": template_data,
                                "id": "cbplan-request-status",
                                "params": params,
                                "type": "email",
                                "config": {
                                    "subject": subject,
                                    "sender": "",
                                },
                            },
                        },
                    }
                ]
            }
        }

        try:
            result = await self._send(payload)
            logger.info(f"CBP plan status email sent to {to_email}: {result}")
        except Exception as exc:
            logger.error(f"Failed to send CBP plan status email to {to_email}: {exc}")

notification_service = NotificationService()
