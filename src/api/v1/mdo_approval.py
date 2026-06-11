"""
MDO Approval API endpoints.
Allows MDO admins to view, approve, and reject approval requests.
"""
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.auth import require_role
from ...core.database import get_db_session
from ...core.logger import logger
from ...controller.mdo_approval import mdo_approval_controller
from ...schemas.mdo_approval import (
    ApprovalRequestListItem,
    ApprovalRequestDetail,
    ApproveRequestBody,
    RetryPublishItemBody,
    RejectRequestBody,
    RejectItemBody,
    UpdateItemBody,
    AddCourseBody,
    RemoveCourseBody,
    ApprovalActionResponse,
    ItemPublishResult,
    RejectActionResponse,
    PaginatedApprovalRequestsResponse,
    PaginationMetadata,
    ApprovalRequestFilters,
)

router = APIRouter(
    prefix="/mdo",
    tags=["MDO Approval"],
)

@router.get("/approval-requests/list", response_model=PaginatedApprovalRequestsResponse)
async def get_approval_requests(
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: Optional[str] = Query(None, description="Search across request name, request ID, state center, department, and requestor email"),
    status_filter: Optional[str] = Query(None, description="Filter by status (pending, approved, rejected)"),
    from_date: Optional[str] = Query(None, description="Filter from date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Filter to date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Get paginated list of approval requests assigned to the MDO.
    Search parameter searches across request name, request ID, state center name, department, and requestor email.
    Supports filtering by status and date range.
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
    Get detailed view of a specific approval request including all designations and related data.
    """
    mdo_id = auth[0]
    try:
        request = await mdo_approval_controller.get_request_detail(
            db=db, request_id=request_id, mdo_id=mdo_id
        )

        if not request:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request not found"
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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Approve and publish all items in an approval request.
    Creates a CBP plan via the external iGOT API and persists the returned igot_cbp_plan_id.
    Generates approval notifications for stakeholders.
    """
    mdo_id, token, approver_name, *_ = auth
    try:
        logger.info(f"Publishing approval request {body.request_id} by MDO {mdo_id} with plan name '{body.plan_name}'")
        item_results = await mdo_approval_controller.publish(
            db=db,
            request_id=body.request_id,
            mdo_id=mdo_id,
            plan_name=body.plan_name,
            due_date=body.due_date.date(),
            token=token,
            approver_name=approver_name,
            approver_id=mdo_id,
            background_tasks=background_tasks,
        )

        items_processed = len(item_results)
        items_succeeded = sum(1 for r in item_results if r["status"] == "success")
        items_failed = sum(1 for r in item_results if r["status"] == "failed")

        logger.info(
            f"Published {items_succeeded}/{items_processed} item(s) for request {body.request_id}"
        )

        return ApprovalActionResponse(
            message=f"CBP plan published: {items_succeeded} succeeded, {items_failed} failed",
            request_status="approved",
            items_processed=items_processed,
            items_succeeded=items_succeeded,
            items_failed=items_failed,
            results=[ItemPublishResult(**r) for r in item_results],
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error in publish_request")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to publish request.",
        )

@router.post("/approval-requests/publish/retry", response_model=ItemPublishResult)
async def retry_publish_item(
    body: RetryPublishItemBody,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Retry publishing a single failed designation item from an already-approved request.
    Used when a previous publish attempt failed for an individual item.
    """
    mdo_id, token, *_ = auth
    try:
        logger.info(f"Retrying publish for item {body.item_id} in request {body.request_id} by MDO {mdo_id}")
        result = await mdo_approval_controller.retry_publish_item(
            db=db,
            request_id=body.request_id,
            item_id=body.item_id,
            mdo_id=mdo_id,
            token=token,
        )

        return ItemPublishResult(**result)

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error in retry_publish_item")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retry publish item.",
        )

@router.post("/approval-requests/reject", response_model=RejectActionResponse)
async def reject_request(
    body: RejectRequestBody,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Reject all designations in an approval request with comments.
    Marks the entire request as rejected and notifies requestor.
    """
    mdo_id, token, rejector_name, *_ = auth
    try:
        logger.info(f"Rejecting approval request {body.request_id} by MDO {mdo_id} with comment '{body.rejection_comment}'")
        updated_request, items_count = await mdo_approval_controller.reject_request(
            db=db,
            request_id=body.request_id,
            mdo_id=mdo_id,
            comments=body.rejection_comment,
            rejector_name=rejector_name,
            rejector_id=mdo_id,
            background_tasks=background_tasks,
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
    Reject a specific designation item in an approval request with comments.
    Re-calculates parent request status based on remaining pending/approved/rejected items.
    """
    mdo_id = auth[0]
    try:
        logger.info(f"Rejecting item {body.item_id} from request {body.request_id} by MDO {mdo_id} with comment '{body.rejection_comment}'")
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
        if error == "item_not_pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Item is not in a pending state and cannot be rejected"
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
    
@router.put("/approval-requests/items/update")
async def update_approval_request_item(
    body: UpdateItemBody,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Update role mapping fields (designation name, competencies, etc.) on a pending designation item.
    Can only update items in PENDING status before approval.
    """
    mdo_id = auth[0]
    try:
        logger.info(f"Updating item {body.item_id} in request {body.request_id} by MDO {mdo_id} with data {body.dict(exclude_unset=True)}")
        update_data = body.model_dump(exclude={"request_id", "item_id"}, exclude_unset=True)
        result, error = await mdo_approval_controller.update_item(
            db=db,
            request_id=body.request_id,
            item_id=body.item_id,
            mdo_id=mdo_id,
            update_data=update_data,
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
                detail=f"Cannot update item in request with status '{current_status}'. Must be 'pending'."
            )
        if error == "item_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request item not found"
            )
        if error == "item_not_pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot update a non-pending item"
            )
        if error == "no_fields_to_update":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields provided to update"
            )

        logger.info(f"Updated item {body.item_id} in request {body.request_id}")

        return {
            "message": "Item updated successfully",
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error updating approval request item")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update approval request item"
        )

@router.post("/approval-requests/course/add")
async def add_course_to_approval_request(
    body: AddCourseBody,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Add courses from the iGOT knowledge base to a designation item's competency-based plan.
    Searches iGOT and appends course details to the CBP plan data.
    """
    mdo_id = auth[0]
    try:
        logger.info(f"Adding courses {body.identifiers} to item {body.item_id} in request {body.request_id} by MDO {mdo_id}")
        result, error = await mdo_approval_controller.add_course_to_item(
            db=db,
            request_id=body.request_id,
            item_id=body.item_id,
            mdo_id=mdo_id,
            identifiers=body.identifiers,
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
                detail=f"Cannot modify request with status '{current_status}'. Must be 'pending'."
            )
        if error == "item_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request item not found"
            )
        if error == "no_cbp_plan_data":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No CBP plan data available for this item"
            )
        if error == "course_already_exists":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="All provided courses already exist in the plan"
            )
        if error == "course_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No courses found for the provided identifiers"
            )

        logger.info(f"Added {result['count']} course(s) to item {body.item_id} in request {body.request_id}")  # type: ignore[index]

        return {
            "message": f"Successfully added {result['count']} course(s)",  # type: ignore[index]
            "request_id": body.request_id,
            "item_id": body.item_id,
            "identifiers_added": result["identifiers_added"],  # type: ignore[index]
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error adding course to approval request")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add course to approval request"
        )
    
@router.post("/approval-requests/course/remove")
async def remove_course_from_approval_request(
    body: RemoveCourseBody,
    db: AsyncSession = Depends(get_db_session),
    auth: tuple = Depends(require_role(['MDO_ADMIN','MDO_LEADER'])),
):
    """
    Remove a course from a designation item's competency-based plan.
    Removes the course by identifier from the CBP plan data.
    """
    mdo_id = auth[0]
    try:
        logger.info(f"Removing course {body.identifier} from item {body.item_id} in request {body.request_id} by MDO {mdo_id}")
        result, error = await mdo_approval_controller.remove_course_from_item(
            db=db,
            request_id=body.request_id,
            item_id=body.item_id,
            mdo_id=mdo_id,
            identifier=body.identifier,
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
                detail=f"Cannot modify request with status '{current_status}'. Must be 'pending'."
            )
        if error == "item_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request item not found"
            )
        if error == "no_cbp_plan_data":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No CBP plan data available for this item"
            )
        if error == "course_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Course with identifier '{body.identifier}' not found in CBP plan data"
            )

        logger.info(f"Removed course {body.identifier} from item {body.item_id} in request {body.request_id}")

        return {
            "message": f"Successfully removed course '{body.identifier}'",
            "request_id": body.request_id,
            "item_id": body.item_id,
            "identifier": body.identifier # type: ignore[index]
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error removing course from approval request")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove course from approval request"
        )
        