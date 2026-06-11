"""
Controller for MDO approval workflows.
Orchestrates CRUD operations and external service calls.
"""
import uuid
from datetime import date, datetime, timezone
from typing import Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import sessionmanager
from ..core.logger import logger
from ..crud.mdo_approval_request import crud_mdo_approval_request
from ..models.mdo_approval import ApprovalRequestRead, ApprovalRequestItemRead
from ..schemas.comman import ApprovalItemStatus
from ..services.igot_service import call_igot_create, call_igot_publish, extract_content_ids
from ..services.notification_service import notification_service

IST = ZoneInfo("Asia/Kolkata")


class MDOApprovalController:
    """
    Business logic for MDO approval workflows.
    Coordinates between CRUD (database) and Service (external API) layers.
    """

    async def list_requests(
        self,
        db: AsyncSession,
        mdo_id: str,
        page: int = 1,
        page_size: int = 10,
        search: Optional[str] = None,
        status_filter: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Tuple[List[ApprovalRequestRead], int]:
        """List approval requests with pagination and filters."""
        normalized_status = status_filter.upper() if status_filter else None

        return await crud_mdo_approval_request.list_mdo_requests(
            db=db,
            mdo_id=mdo_id,
            page=page,
            page_size=page_size,
            search=search,
            status_filter=normalized_status,
            from_date=from_date,
            to_date=to_date,
        )

    async def get_request_detail(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        mdo_id: str,
    ) -> Optional[ApprovalRequestRead]:
        """Get a single approval request with items."""
        return await crud_mdo_approval_request.get_by_request_id_and_mdo(
            db=db, request_id=request_id, mdo_id=mdo_id
        )

    async def _publish_single_item(
        self,
        item: ApprovalRequestItemRead,
        token: str,
        org_id: str,
        plan_name: str,
        due_date: date,
    ) -> dict:
        """
        Attempt to create and publish a CBP plan for a single item.
        Retries up to MAX_PUBLISH_RETRIES times on failure.

        Returns a result dict with item_id, designation_name, status, and plan_id.
        """
        designation = item.igot_designation_name or item.designation_name
        content_ids: List[str] = []
        if item.cbp_plan_data:
            content_ids = extract_content_ids(item.cbp_plan_data)

        if not content_ids:
            logger.warning(
                f"No content IDs found for item {item.id} ({designation}). "
                "cbp_plan_data may be empty or missing selected_courses."
            )
            return {
                "item_id": str(item.id),
                "designation_name": designation,
                "status": "failed",
                "plan_id": None,
                "error": "No CBP Plan found for this item.",
            }

        try:
            igot_cbp_plan_id_str = await call_igot_create(
                token=token,
                org_id=org_id,
                plan_name=plan_name,
                due_date=due_date,
                designations=[designation],
                content_ids=content_ids,
                is_apar=False,
            )

            await call_igot_publish(
                token=token,
                org_id=org_id,
                plan_id=igot_cbp_plan_id_str,
            )

            return {
                "item_id": str(item.id),
                "designation_name": designation,
                "status": "success",
                "plan_id": igot_cbp_plan_id_str,
                "error": None,
            }
        except HTTPException as e:
            last_error = e.detail
            logger.warning(
                f"Publish failed for item {item.id} "
                f"({designation}): {e.detail}"
            )
        except Exception as e:
            last_error = str(e)
            logger.warning(
                f"Publish failed for item {item.id} "
                f"({designation}): {e}"
            )

        return {
            "item_id": str(item.id),
            "designation_name": designation,
            "status": "failed",
            "plan_id": None,
            "error": 'iGOT CBP plan creation/publish failed.',
        }

    async def publish(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        mdo_id: str,
        plan_name: str,
        due_date: date,
        token: str,
        approver_name: str = "",
        approver_id: str = "",
        background_tasks: BackgroundTasks = None,
    ) -> Tuple[Optional[ApprovalRequestRead], List[dict]]:
        """
        Approve and publish a pending approval request per-item.

        Each item gets its own CBP plan (create + publish). Failed items are
        retried up to MAX_PUBLISH_RETRIES times. Results are tracked per item.

        Order of operations:
          1. Lock + validate the request is PENDING
          2. For each item: create + publish plan (with retry)
          3. Persist successful items as APPROVED with their plan_id
          4. Mark the request as APPROVED

        Returns:
            (updated request, item_results) where item_results contains per-item
            status with item_id, designation_name, status, plan_id, and error.
        """
        # 1. Lock and fetch the request
        request = await crud_mdo_approval_request.get_pending_for_update(
            db=db, request_id=request_id, mdo_id=mdo_id
        )
        if not request:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request not found or not in PENDING status.",
            )

        org_id = request.department_id if request.department_id else request.state_center_id

        # 2. Publish per item (only PENDING items)
        item_results: List[dict] = []
        for item in request.items:
            if item.status != ApprovalItemStatus.PENDING:
                continue
            result = await self._publish_single_item(
                item=item,
                token=token,
                org_id=org_id,
                plan_name=plan_name,
                due_date=due_date,
            )
            item_results.append(result)

        if not item_results:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No items in PENDING status to publish.",
            )

        # 3. Persist successful items and approve the request
        successful_items = [
            r for r in item_results if r["status"] == "success"
        ]

        if not successful_items:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="All items failed to publish. Please try again later.",
            )

        await crud_mdo_approval_request.persist_approval_per_item(
            db=db,
            request=request,
            request_id=request_id,
            mdo_id=mdo_id,
            plan_name=plan_name,
            due_date=due_date,
            item_results=item_results,
        )

        # Send approval email in background
        if background_tasks:
            background_tasks.add_task(
                self._send_cbplan_status_email,
                request, plan_name, "Approved", approver_name, approver_id, None
            )

        return item_results

    async def reject_request(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        mdo_id: str,
        comments: str,
        rejector_name: str = "",
        rejector_id: str = "",
        background_tasks: BackgroundTasks = None,
    ) -> Tuple[Optional[ApprovalRequestRead], int]:
        """
        Reject entire approval request (all items).

        Returns:
            (updated request, items_rejected_count) or (None, 0) if not found/not PENDING
        """
        updated_request, items_count = await crud_mdo_approval_request.reject_request(
            db=db,
            request_id=request_id,
            mdo_id=mdo_id,
            comments=comments,
        )

        # Send rejection email in background
        if updated_request and background_tasks:
            background_tasks.add_task(
                self._send_cbplan_status_email,
                updated_request, updated_request.request_name, "Rejected", rejector_name, rejector_id, comments
            )

        return updated_request, items_count

    async def _send_cbplan_status_email(
        self,
        request: ApprovalRequestRead,
        cbp_name: str,
        email_status: str,
        action_by_name: str,
        action_by_id: str,
        rejection_reason: Optional[str],
    ) -> None:
        """Send CBP plan status email notification in background."""
        try:
            from ..models.user import User
            from sqlalchemy.future import select

            async with sessionmanager.session() as db:
                stmt = select(User).where(User.user_id == request.user_id)
                result = await db.execute(stmt)
                user = result.scalar_one_or_none()

            if not user or not user.email:
                logger.warning(f"Cannot send CBP status email: user email not found for request {request.id}")
                return

            action_date = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
            await notification_service.send_cbplan_status_email(
                to_email=user.email,
                cbp_name=cbp_name,
                status=email_status,
                approver_name=action_by_name,
                action_date=action_date,
                rejection_reason=rejection_reason if rejection_reason else "N/A",
                user_id=action_by_id,
            )
        except Exception:
            logger.exception(f"Error sending CBP plan status email for request {request.id}")

    async def reject_single_item(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        item_id: uuid.UUID,
        mdo_id: str,
        comments: str,
    ) -> Tuple[Optional[dict[str, str]], Optional[str]]:
        """
        Reject a specific item and recalculate parent request status.

        Returns:
            (result_dict, error_message)
        """
        return await crud_mdo_approval_request.reject_single_item(
            db=db,
            request_id=request_id,
            item_id=item_id,
            mdo_id=mdo_id,
            comments=comments,
        )
        
    async def add_course_to_item(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        item_id: uuid.UUID,
        mdo_id: str,
        identifiers: List[str],
    ) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
        """
        Add course identifiers to an item's cbp_plan_data.

        Returns:
            (result_dict, error_message)
        """
        return await crud_mdo_approval_request.add_course_to_item(
            db=db,
            request_id=request_id,
            item_id=item_id,
            mdo_id=mdo_id,
            identifiers=identifiers,
        )
        

    async def remove_course_from_item(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        item_id: uuid.UUID,
        mdo_id: str,
        identifier: str,
    ) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
        """
        Remove a course identifier from an item's cbp_plan_data.

        Returns:
            (result_dict, error_message)
        """
        return await crud_mdo_approval_request.remove_course_from_item(
            db=db,
            request_id=request_id,
            item_id=item_id,
            mdo_id=mdo_id,
            identifier=identifier,
        )

    async def update_item(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        item_id: uuid.UUID,
        mdo_id: str,
        update_data: dict,
    ) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
        """
        Update role mapping fields on a specific approval request item.

        Returns:
            (result_dict, error_message)
        """
        return await crud_mdo_approval_request.update_item(
            db=db,
            request_id=request_id,
            item_id=item_id,
            mdo_id=mdo_id,
            update_data=update_data,
        )

    async def retry_publish_item(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        item_id: uuid.UUID,
        mdo_id: str,
        token: str,
    ) -> dict:
        """
        Retry publishing a single failed item from an already-approved request.
        Reads plan_name and due_date from the existing MdoApproval record.

        Returns a result dict with item_id, designation_name, status, plan_id, and error.
        """
        mdo_approval_record, item = await crud_mdo_approval_request.get_failed_item_for_retry(
            db=db, request_id=request_id, item_id=item_id, mdo_id=mdo_id
        )

        if not mdo_approval_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval record not found for this item.",
            )

        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found or not in FAILED status.",
            )

        # Read org_id from the parent request
        request = await crud_mdo_approval_request.get_by_request_id_and_mdo(
            db=db, request_id=request_id, mdo_id=mdo_id
        )
        if not request:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request not found.",
            )

        org_id = request.department_id if request.department_id else request.state_center_id
        plan_name = mdo_approval_record.plan_name
        due_date = mdo_approval_record.due_date.date() if mdo_approval_record.due_date else date.today()

        result = await self._publish_single_item(
            item=item,
            token=token,
            org_id=org_id,
            plan_name=plan_name,
            due_date=due_date,
        )

        if result["status"] == "success":
            await crud_mdo_approval_request.persist_retry_item_success(
                db=db,
                mdo_approval_id=mdo_approval_record.id,
                item_id=item_id,
                igot_cbp_plan_id=result["plan_id"],
            )

        return result


mdo_approval_controller = MDOApprovalController()
