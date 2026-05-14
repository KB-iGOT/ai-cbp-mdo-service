"""
MDO Approval API endpoints.
Allows MDO admins to view, approve, and reject approval requests.
"""
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.auth import require_role
from ...core.database import get_db_session
from ...core.logger import logger
from ...controller.mdo_approval import mdo_approval_controller
from ...schemas.mdo_approval import (
    ApprovalRequestListItem,
    ApprovalRequestDetail,
    ApproveRequestBody,
    RejectRequestBody,
    RejectItemBody,
    ApprovalActionResponse,
    RejectActionResponse,
    PaginatedApprovalRequestsResponse,
    PaginationMetadata,
    ApprovalRequestFilters
)

router = APIRouter(
    prefix="/mdo",
    tags=["MDO Approval"],
)


@router.get("/approval-requests/list", response_model=PaginatedApprovalRequestsResponse)
async def get_approval_requests(
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: Optional[str] = Query(None, description="Search by request name or state/center name"),
    status_filter: Optional[str] = Query(None, description="Filter by status (pending, approved, rejected)"),
    from_date: Optional[str] = Query(None, description="Filter from date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Filter to date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Get paginated list of approval requests for the MDO.
    Supports search and filtering by status and date range.
    """
    mdo_id = auth[0]
    try:
        items, total_count = await mdo_approval_controller.list_requests(
            db=db,
            mdo_id=mdo_id,
            page=page,
            page_size=page_size,
            search=search,
            status_filter=status_filter,
            from_date=from_date,
            to_date=to_date
        )

        return PaginatedApprovalRequestsResponse(
            items=[ApprovalRequestListItem.model_validate(item) for item in items],
            pagination=PaginationMetadata(
                current_page=page,
                page_size=page_size,
                total_items=total_count
            )
        )
    except Exception:
        logger.exception(f"Error fetching approval requests for MDO {mdo_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch approval requests"
        )


@router.get("/approval-requests/read/{request_id}", response_model=ApprovalRequestDetail)
async def get_approval_request_detail(
    request_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Get detailed view of a specific approval request with all items.
    """
    mdo_id = auth[0]
    try:
        request = await mdo_approval_controller.get_request_detail(
            db=db, request_id=request_id, mdo_id=mdo_id
        )

        if not request:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request not found or access denied"
            )

        return ApprovalRequestDetail.model_validate(request)

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error fetching approval request detail")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch approval request details"
        )


@router.post("/approval-requests/publish", response_model=ApprovalActionResponse)
async def publish_request(
    body: ApproveRequestBody,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Approve all items in an approval request, create a CBP plan via the
    external API, and persist the returned igot_cbp_plan_id against each MdoApproval row.
    """
    mdo_id, token = auth
    try:
        updated_request, igot_cbp_plan_id_str = await mdo_approval_controller.publish(
            db=db,
            request_id=body.request_id,
            mdo_id=mdo_id,
            plan_name=body.plan_name,
            due_date=body.due_date.date(),
            token=token,
        )

        if updated_request is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request not found, access denied, or not in PENDING status.",
            )

        items_processed = len(updated_request.items) if updated_request.items else 0
        item_ids = [item.id for item in updated_request.items] if updated_request.items else []

        logger.info(
            f"Approved {items_processed} item(s) for request {body.request_id} | "
            f"igot_cbp_plan_id={igot_cbp_plan_id_str}"
        )

        return ApprovalActionResponse(
            message="CBP plan created successfully",
            request_status="approved",
            items_processed=items_processed,
            item_ids=item_ids,
            igot_cbp_plan_id=igot_cbp_plan_id_str,
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error in publish_request")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to publish request.",
        )


@router.post("/approval-requests/reject", response_model=RejectActionResponse)
async def reject_request(
    body: RejectRequestBody,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Reject all items in an approval request.
    """
    mdo_id = auth[0]
    try:
        updated_request, items_count = await mdo_approval_controller.reject_request(
            db=db,
            request_id=body.request_id,
            mdo_id=mdo_id,
            comments=body.rejection_comment,
        )

        if updated_request is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request not found, access denied, or not in PENDING status."
            )

        item_ids = [item.id for item in updated_request.items] if updated_request.items else []

        logger.info(f"Rejected {items_count} items for request {body.request_id}")

        return RejectActionResponse(
            message=f"Successfully rejected {items_count} designation(s)",
            request_status="rejected",
            items_processed=items_count,
            item_ids=item_ids,
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error rejecting request")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reject request"
        )


@router.post("/approval-requests/items/reject")
async def reject_approval_request_item(
    body: RejectItemBody,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Reject a specific item in an approval request with comments.
    """
    mdo_id = auth[0]
    try:
        result, error = await mdo_approval_controller.reject_single_item(
            db=db,
            request_id=body.request_id,
            item_id=body.item_id,
            mdo_id=mdo_id,
            comments=body.rejection_comment,
        )

        if error == "not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request not found or access denied"
            )
        if error and error.startswith("invalid_status:"):
            current_status = error.split(":", 1)[1]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reject item in request with status '{current_status}'. Must be 'pending'."
            )
        if error == "item_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request item not found"
            )
        if error == "already_rejected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Item is already rejected"
            )

        logger.info(f"Rejected item {body.item_id} from request {body.request_id}")

        return {
            "message": f"Successfully rejected designation '{result['designation_name']}'",  # type: ignore[index]
            "request_id": body.request_id,
            "item_id": body.item_id,
            "request_status": result["request_status"],  # type: ignore[index]
            "rejection_comment": body.rejection_comment
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error rejecting approval request item")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reject approval request item"
        )