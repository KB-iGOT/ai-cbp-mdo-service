"""
Designation Approval API endpoints (SPV Admin flow).
Allows SPV admins to view, approve, and reject designation approval requests
submitted from the CBP portal.
"""
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.auth import require_role
from ...core.database import get_db_session
from ...core.logger import logger
from ...controller.designation_approval import designation_approval_controller
from ...schemas.designation_approval import (
    ApproveDesignationBody,
    RejectDesignationBody,
    DesignationApprovalItem,
    DesignationApprovalActionResponse,
    PaginatedDesignationApprovalResponse,
    PaginationMetadata,
)

router = APIRouter(prefix="/designation", tags=["SPV Designation Approval"])


@router.get("/approval-requests/list", response_model=PaginatedDesignationApprovalResponse)
async def list_designation_approvals(
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: Optional[str] = Query(None, description="Search by designation name, organisation, or email"),
    status_filter: Optional[str] = Query(None, description="Filter by status: pending, approved, rejected"),
    from_date: Optional[str] = Query(None, description="Filter from date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Filter to date (YYYY-MM-DD)"),
    org_id: Optional[str] = Query(None, description="Filter by organisation ID (state_center_id or department_id)"),
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['SPV_ADMIN','MDO_ADMIN','MDO_LEADER'])),
):
    """
    Get paginated list of designation approval requests for SPV admin.
    Supports search and filtering by status and date range.
    """
    try:
        items, total_count = await designation_approval_controller.list_approvals(
            db=db,
            page=page,
            page_size=page_size,
            search=search,
            status_filter=status_filter,
            from_date=from_date,
            to_date=to_date,
            org_id=org_id,
        )

        return PaginatedDesignationApprovalResponse(
            items=[DesignationApprovalItem.model_validate(item) for item in items],
            pagination=PaginationMetadata(
                current_page=page,
                page_size=page_size,
                total_items=total_count,
            ),
        )
    except Exception:
        logger.exception("Error fetching designation approvals")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch designation approvals",
        )


@router.post("/approval-requests/approve", response_model=DesignationApprovalActionResponse)
async def approve_designation(
    body: ApproveDesignationBody,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['SPV_ADMIN'])),
):
    """
    Approve a single designation approval request.
    Only PENDING records will be updated.
    Returns the created designation ID from iGOT.
    """
    try:
        logger.info(f"Approving designation approval request {body.id} by user {auth[0]}")
        user_id, token, approver_name, org = auth

        success, message, designation_id = await designation_approval_controller.approve(
            db=db,
            record_id=body.id,
            approver_name=approver_name,
            approver_id=user_id,
            token=token,
            background_tasks=background_tasks,
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Designation approval not found or already processed.",
            )

        return DesignationApprovalActionResponse(
            message=message,
            status="approved",
            id=body.id,
            designation_id=designation_id,
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error approving designation approval")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to approve designation approval",
        )


@router.post("/approval-requests/reject", response_model=DesignationApprovalActionResponse)
async def reject_designation(
    body: RejectDesignationBody,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['SPV_ADMIN'])),
):
    """
    Reject a single designation approval request.
    Only PENDING records will be updated.
    """
    try:
        user_id, token, rejector_name, org = auth
        logger.info(f"Rejecting designation approval request {body.id} by user {user_id} with comment '{body.reviewer_comments}'")

        success = await designation_approval_controller.reject(
            db=db,
            record_id=body.id,
            reviewer_comments=body.reviewer_comments,
            rejector_name=rejector_name,
            rejector_id=user_id,
            background_tasks=background_tasks,
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Designation approval not found or already processed.",
            )

        return DesignationApprovalActionResponse(
            message="Successfully rejected",
            status="rejected",
            id=body.id,
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error rejecting designation approval")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reject designation approval",
        )
