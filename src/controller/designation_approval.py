"""
Controller for Designation Approval workflows (SPV Admin).
Orchestrates CRUD operations and business logic.
"""
import uuid
from datetime import datetime, date
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logger import logger
from ..crud.designation_approval import crud_designation_approval
from ..models.designation_approval import DesignationApproval
from ..services.igot_service import call_igot_create_designation
from ..services.notification_service import notification_service

IST = ZoneInfo("Asia/Kolkata")

class DesignationApprovalController:
    """
    Business logic for designation approval workflows.
    Coordinates between API layer and CRUD (database) layer.
    """

    async def list_approvals(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 10,
        search: Optional[str] = None,
        status_filter: Optional[str] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        org_id: Optional[str] = None,
    ) -> Tuple[List[DesignationApproval], int]:
        """
        List designation approvals with pagination and filters.
        
        Returns:
            (items, total_count)
        """
        return await crud_designation_approval.list_designation_approvals(
            db=db,
            page=page,
            page_size=page_size,
            search=search,
            status_filter=status_filter,
            from_date=from_date,
            to_date=to_date,
            org_id=org_id,
        )

    async def approve(
        self,
        db: AsyncSession,
        record_id: uuid.UUID,
        approver_name: str,
        approver_id: str,
        token: str,
        background_tasks: BackgroundTasks,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Approve a single designation approval request.

        Business Logic:
          1. Fetch the PENDING record (verify it exists)
          2. Call iGOT designation master Create API
             - On failure: return error (DB untouched)
          3. On iGOT success: update DB status to APPROVED and commit
          4. Send approval email notification (background)

        Returns:
            (True, message, designation_id) if approved, (False, None, None) if not found or already processed
        """
        logger.info(f"Approving designation approval: {record_id}")

        # Step 1: Verify record exists and is PENDING
        record = await crud_designation_approval.get_pending(db=db, record_id=record_id)
        if not record:
            logger.warning(f"Failed to approve designation approval: {record_id} (not found or already processed)")
            return False, None, None

        designation_name = record.designation_name

        # Step 2: Call iGOT designation master Create API first
        try:
            result = await call_igot_create_designation(
                token=token,
                designation=designation_name,
            )
        except Exception:
            logger.exception(f"iGOT designation create failed for {record_id}")
            raise HTTPException(
                status_code=502,
                detail="Failed to create designation in iGOT master list. Please try again later.",
            )

        # Extract designation ID from nested iGOT response: result.result.id
        designation_id = result.get("result", {}).get("id")

        # Determine response message
        if result.get("already_present"):
            logger.info(f"Designation '{designation_name}' already present in iGOT master list for {record_id}")
            message = "Successfully approved. Designation is already present in the master list."
        else:
            message = "Successfully approved"

        # Step 3: iGOT succeeded — now save approval to DB
        success = await crud_designation_approval.approve(db=db, record_id=record_id)
        if not success:
            logger.warning(f"Failed to save approval for {record_id} (concurrent update)")
            return False, None, None

        logger.info(f"Successfully approved designation approval: {record_id} with iGOT ID: {designation_id}")

        # Step 4: Send email notification in background
        background_tasks.add_task(
            self._send_approval_email, record_id, approver_name, approver_id
        )
        return True, message, designation_id

    async def _send_approval_email(
        self,
        record_id: uuid.UUID,
        approver_name: str,
        approver_id: str,
    ) -> None:
        """Send approval email notification in background."""
        try:
            record = await crud_designation_approval.get_by_id(record_id)
            if not record or not record.user or not record.user.email:
                logger.warning(f"Cannot send approval email: record or user email not found for {record_id}")
                return

            approval_date = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
            await notification_service.send_designation_approved_email(
                to_email=record.user.email,
                designation_name=record.designation_name,
                approver_name=approver_name,
                approval_date=approval_date,
                user_id=approver_id,
            )
        except Exception:
            logger.exception(f"Error sending approval email for {record_id}")

    async def reject(
        self,
        db: AsyncSession,
        record_id: uuid.UUID,
        reviewer_comments: Optional[str] = None,
        rejector_name: str = "",
        rejector_id: str = "",
        background_tasks: BackgroundTasks = None,
    ) -> bool:
        """
        Reject a single designation approval request.
        
        Business Logic:
          1. Lock and fetch the PENDING record
          2. Validate state (PENDING only)
          3. Update status to REJECTED with comments
          4. Commit transaction
          5. Send rejection email notification (background)
        
        Args:
            db: Database session
            record_id: UUID of designation approval to reject
            reviewer_comments: Optional reason for rejection
            rejector_name: Name of the rejector from token
            rejector_id: User ID of the rejector from token
            background_tasks: FastAPI BackgroundTasks instance
        
        Returns:
            True if rejected, False if not found or already processed
        """
        logger.info(f"Rejecting designation approval: {record_id}")
        success = await crud_designation_approval.reject(
            db=db,
            record_id=record_id,
            reviewer_comments=reviewer_comments,
        )
        
        if success:
            logger.info(f"Successfully rejected designation approval: {record_id}")
            # Send email notification in background
            if background_tasks:
                background_tasks.add_task(
                    self._send_rejection_email, record_id, rejector_name, rejector_id, reviewer_comments
                )
        else:
            logger.warning(f"Failed to reject designation approval: {record_id} (not found or already processed)")
        
        return success

    async def _send_rejection_email(
        self,
        record_id: uuid.UUID,
        rejector_name: str,
        rejector_id: str,
        rejection_reason: Optional[str],
    ) -> None:
        """Send rejection email notification in background."""
        try:
            record = await crud_designation_approval.get_by_id(record_id)
            if not record or not record.user or not record.user.email:
                logger.warning(f"Cannot send rejection email: record or user email not found for {record_id}")
                return

            rejection_date = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
            await notification_service.send_designation_rejected_email(
                to_email=record.user.email,
                designation_name=record.designation_name,
                rejector_name=rejector_name,
                rejection_date=rejection_date,
                rejection_reason=rejection_reason if rejection_reason else "N/A",
                user_id=rejector_id,
            )
        except Exception:
            logger.exception(f"Error sending rejection email for {record_id}")


designation_approval_controller = DesignationApprovalController()
