"""
CRUD operations for MDO Portal approval request management
"""
import uuid
from datetime import datetime, date, timezone
from typing import Any, List, Optional, Tuple, Dict
import httpx

from sqlalchemy import and_, desc, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload, noload

from ..models.mdo_approval import ApprovalRequestRead, ApprovalRequestItemRead, MdoApproval
from ..models.user import User
from ..schemas.comman import ApprovalStatus, ApprovalItemStatus
from ..core.configs import settings


class CRUDMDOApprovalRequest:
    """
    CRUD methods for MDO Portal to manage approval requests
    """

    async def list_mdo_requests(
        self,
        db: AsyncSession,
        mdo_id: str,
        page: int = 1,
        page_size: int = 10,
        search: Optional[str] = None,
        status_filter: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None
    ) -> Tuple[List[ApprovalRequestRead], int]:
        """
        List approval requests assigned to a specific MDO with pagination and filters.
        Search parameter searches across: request name, request ID, state center name, 
        department name, and requestor email.
        Returns (items, total_count).
        """
        from sqlalchemy import or_
        
        conditions = [
            ApprovalRequestRead.mdo_id == mdo_id,
            ApprovalRequestRead.status != ApprovalStatus.DRAFT.value,
        ]

        # Search: partial match across multiple fields
        if search:
            search_term = search.strip()
            search_conditions = [
                ApprovalRequestRead.request_name.ilike(f"%{search_term}%"),
                ApprovalRequestRead.state_center_name.ilike(f"%{search_term}%"),
            ]
            
            # Try to match request ID if search term looks like a UUID
            try:
                search_uuid = uuid.UUID(search_term)
                search_conditions.append(ApprovalRequestRead.id == search_uuid)
            except (ValueError, AttributeError):
                pass
            
            # Include department name if not null
            search_conditions.append(
                ApprovalRequestRead.department_name.ilike(f"%{search_term}%")
            )
            
            # Include requestor email via user relationship
            search_conditions.append(
                ApprovalRequestRead.user.has(User.email.ilike(f"%{search_term}%"))
            )
            
            conditions.append(or_(*search_conditions))

        # Status filter
        if status_filter:
            conditions.append(ApprovalRequestRead.status == status_filter)

        # Date range filter on created_at
        if from_date:
            conditions.append(ApprovalRequestRead.created_at >= from_date)
        if to_date:
            conditions.append(ApprovalRequestRead.created_at <= to_date)

        where_clause = and_(*conditions)

        # Count total
        count_stmt = select(func.count()).select_from(
            ApprovalRequestRead).where(where_clause)
        count_result = await db.execute(count_stmt)
        total = count_result.scalar_one()

        # Fetch page - Don't load relationships to avoid field mismatch issues
        offset = (page - 1) * page_size
        stmt = (
            select(ApprovalRequestRead)
            .options(
                noload(ApprovalRequestRead.items),
                selectinload(ApprovalRequestRead.user),
            )
            .where(where_clause)
            .order_by(desc(ApprovalRequestRead.created_at))
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def get_by_request_id_and_mdo(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        mdo_id: str
    ) -> Optional[ApprovalRequestRead]:
        """
        Get an approval request by its UUID and MDO ID.
        Ensures the request is assigned to the specified MDO.
        """
        stmt = (
            select(ApprovalRequestRead)
            .options(
                selectinload(ApprovalRequestRead.items),
                selectinload(ApprovalRequestRead.user),
            )
            .where(
                and_(
                    ApprovalRequestRead.id == request_id,
                    ApprovalRequestRead.mdo_id == mdo_id
                )
            )
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    async def _get_for_update(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        mdo_id: str,
    ) -> Optional[ApprovalRequestRead]:
        """
        Lock and fetch an approval request with items for update.
        Returns None if not found. Does NOT check status.
        """
        stmt = (
            select(ApprovalRequestRead)
            .options(selectinload(ApprovalRequestRead.items))
            .where(
                and_(
                    ApprovalRequestRead.id == request_id,
                    ApprovalRequestRead.mdo_id == mdo_id,
                )
            )
            .with_for_update()
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    async def get_pending_for_update(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        mdo_id: str,
    ) -> Optional[ApprovalRequestRead]:
        """
        Lock and fetch a PENDING approval request for update.
        Returns None if not found or not PENDING.
        """
        request = await self._get_for_update(db, request_id, mdo_id)
        if not request or request.status != ApprovalStatus.PENDING:
            return None
        return request

    async def persist_approval_per_item(
        self,
        db: AsyncSession,
        request: ApprovalRequestRead,
        request_id: uuid.UUID,
        mdo_id: str,
        plan_name: str,
        due_date: date,
        item_results: list,
    ) -> bool:
        """
        Persist per-item approval results. Each item that was successfully
        published gets APPROVED status and its own igot_cbp_plan_id.
        The parent request is marked APPROVED.

        item_results: list of dicts with keys: item_id, status, plan_id
        """
        now = datetime.now(timezone.utc)
        due_dt = datetime.combine(due_date, datetime.min.time()).replace(tzinfo=timezone.utc)

        # Build a lookup from item_id -> result
        result_map = {r["item_id"]: r for r in item_results}

        # Update the approval request status to APPROVED
        await db.execute(
            update(ApprovalRequestRead)
            .where(
                and_(
                    ApprovalRequestRead.id == request_id,
                    ApprovalRequestRead.mdo_id == mdo_id,
                    ApprovalRequestRead.status == ApprovalStatus.PENDING,
                )
            )
            .values(
                status=ApprovalStatus.APPROVED,
                updated_at=now,
            )
        )

        # Process each item based on its publish result
        for item in request.items:
            item_result = result_map.get(str(item.id))
            if item_result and item_result["status"] == "success":
                igot_cbp_plan_id = uuid.UUID(item_result["plan_id"])
                db.add(
                    MdoApproval(
                        approval_request_id=request_id,
                        approval_request_item_id=item.id,
                        plan_name=plan_name,
                        due_date=due_dt,
                        igot_cbp_plan_id=igot_cbp_plan_id,
                        created_at=now,
                    )
                )
                await db.execute(
                    update(ApprovalRequestItemRead)
                    .where(ApprovalRequestItemRead.id == item.id)
                    .values(status=ApprovalItemStatus.APPROVED)
                )
            else:
                # Item failed to publish - mark as APPROVED but without plan_id
                db.add(
                    MdoApproval(
                        approval_request_id=request_id,
                        approval_request_item_id=item.id,
                        plan_name=plan_name,
                        due_date=due_dt,
                        igot_cbp_plan_id=None,
                        created_at=None,
                    )
                )
                await db.execute(
                    update(ApprovalRequestItemRead)
                    .where(ApprovalRequestItemRead.id == item.id)
                    .values(status=ApprovalItemStatus.FAILED)
                )

        await db.commit()
        return True


    async def reject_request(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        mdo_id: str,
        comments: str,
    ) -> Tuple[Optional[ApprovalRequestRead], int]:
        """
        Reject entire approval request (all designations).

        Returns:
            (updated request, items_rejected_count)
            or (None, 0) if not found / not PENDING
        """
        request = await self.get_pending_for_update(db, request_id, mdo_id)

        if not request:
            return None, 0

        items_count = len(request.items)

        # Update request status
        now = datetime.now(timezone.utc)
        await db.execute(
            update(ApprovalRequestRead)
            .where(
                and_(
                    ApprovalRequestRead.id == request_id,
                    ApprovalRequestRead.mdo_id == mdo_id,
                    ApprovalRequestRead.status == ApprovalStatus.PENDING
                )
            )
            .values(
                status=ApprovalStatus.REJECTED,
                rejected_at=now,
                reviewer_comments=comments,
                updated_at=now,
            )
        )

        # Update all items to rejected status
        await db.execute(
            update(ApprovalRequestItemRead)
            .where(ApprovalRequestItemRead.approval_request_id == request_id)
            .values(
                status=ApprovalItemStatus.REJECTED,
                reviewer_comments=comments,
                rejected_at=now,
            )
        )

        await db.commit()

        updated = await self.get_by_request_id_and_mdo(db, request_id, mdo_id)
        return updated, items_count

    async def reject_single_item(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        item_id: uuid.UUID,
        mdo_id: str,
        comments: str,
    ) -> Tuple[Optional[dict[str, str]], Optional[str]]:
        """
        Reject a specific item within an approval request and recalculate parent status.

        Returns:
            (result_dict, error_message)
            result_dict contains: designation_name, request_status
            error_message is set if validation fails, result_dict is None
        """
        # Need separate not_found vs invalid_status errors, so can't use get_pending_for_update
        request = await self._get_for_update(db, request_id, mdo_id)

        if not request:
            return None, "not_found"

        if request.status != ApprovalStatus.PENDING:
            return None, f"invalid_status:{request.status}"

        # Find the target item
        target_item = None
        for item in request.items:
            if item.id == item_id:
                target_item = item
                break

        if not target_item:
            return None, "item_not_found"

        if target_item.status != ApprovalStatus.PENDING:
            return None, "item_not_pending"

        # Reject the item
        now = datetime.now(timezone.utc)
        await db.execute(
            update(ApprovalRequestItemRead)
            .where(ApprovalRequestItemRead.id == item_id)
            .values(
                status=ApprovalItemStatus.REJECTED,
                reviewer_comments=comments,
                rejected_at=now,
            )
        )

        # Recalculate parent request status based on all item statuses
        # (use in-memory items, accounting for the one we just rejected)
        pending_count = 0
        approved_count = 0
        rejected_count = 0
        for item in request.items:
            item_status = ApprovalItemStatus.REJECTED if item.id == item_id else item.status
            if item_status == ApprovalItemStatus.PENDING:
                pending_count += 1
            elif item_status == ApprovalItemStatus.APPROVED:
                approved_count += 1
            elif item_status == ApprovalItemStatus.REJECTED:
                rejected_count += 1

        new_status = ApprovalStatus.PENDING
        if pending_count == 0:
            if rejected_count > 0 and approved_count == 0:
                new_status = ApprovalStatus.REJECTED
                await db.execute(
                    update(ApprovalRequestRead)
                    .where(ApprovalRequestRead.id == request_id)
                    .values(
                        status=ApprovalStatus.REJECTED,
                        rejected_at=now,
                        updated_at=now,
                    )
                )
            elif approved_count > 0:
                new_status = ApprovalStatus.APPROVED
                await db.execute(
                    update(ApprovalRequestRead)
                    .where(ApprovalRequestRead.id == request_id)
                    .values(
                        status=ApprovalStatus.APPROVED,
                        updated_at=now,
                    )
                )

        await db.commit()

        return {
            "designation_name": target_item.designation_name,
            "request_status": new_status,
        }, None

    async def search_courses(self, identifiers: List[str]) -> List[Dict[str, Any]]:
        if not identifiers:
            return []

        payload = {
            "request": {
                "filters": {
                    "primaryCategory": ["Course"],
                    "status": ["Live"],
                    "courseCategory": ["Course"],
                    "identifier": identifiers
                },
                "fields": [
                    "name", "identifier", "description", "keywords",
                    "organisation", "competencies_v6", "language", "duration"
                ],
                "sortBy": {"createdOn": "Desc"},
                "limit": 100
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.KB_BASE_URL}/api/content/v1/search",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {settings.KB_AUTH_TOKEN}"
                }
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("result", {}).get("content", [])
            for item in content:
                item["relevancy"] = settings.DEFAULT_RELEVANCY_SCORE
            return content

    async def add_course_to_item(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        item_id: uuid.UUID,
        mdo_id: str,
        identifiers: List[str],
    ) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
        """
        Add courses to an item's cbp_plan_data by searching iGOT and
        appending to selected_courses.

        Returns:
            (result_dict, error_message)
            result_dict contains: item_id, identifiers_added, count
            error_message is set if validation fails
        """
        request = await self._get_for_update(db, request_id, mdo_id)

        if not request:
            return None, "not_found"

        if request.status != ApprovalStatus.PENDING:
            return None, f"invalid_status:{request.status}"

        # Find the target item
        target_item = None
        for item in request.items:
            if item.id == item_id:
                target_item = item
                break

        if not target_item:
            return None, "item_not_found"

        if not target_item.cbp_plan_data:
            return None, "no_cbp_plan_data"

        cbp_data = target_item.cbp_plan_data
        records = cbp_data if isinstance(cbp_data, list) else [cbp_data]

        # Collect existing identifiers
        existing = set()
        for record in records:
            for c in record.get("selected_courses", []):
                existing.add(c.get("identifier"))

        # Filter out already-existing identifiers
        new_identifiers = [i for i in identifiers if i not in existing]
        if not new_identifiers:
            return None, "course_already_exists"

        # Search iGOT for the course data
        courses_data = await self.search_courses(new_identifiers)
        if not courses_data:
            return None, "course_not_found"

        # Append all found courses to selected_courses in first record
        records[0].setdefault("selected_courses", []).extend(courses_data)

        # Persist updated cbp_plan_data
        updated_data = records if isinstance(cbp_data, list) else records[0]
        await db.execute(
            update(ApprovalRequestItemRead)
            .where(ApprovalRequestItemRead.id == item_id)
            .values(cbp_plan_data=updated_data)
        )

        await db.commit()

        added_ids = [c["identifier"] for c in courses_data]
        return {
            "item_id": str(item_id),
            "identifiers_added": added_ids,
            "count": len(added_ids),
        }, None   
        
        
    async def remove_course_from_item(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        item_id: uuid.UUID,
        mdo_id: str,
        identifier: str,
    ) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
        """
        Remove a course (by identifier) from an item's cbp_plan_data.

        Returns:
            (result_dict, error_message)
            result_dict contains: item_id, identifier, remaining_courses_count
            error_message is set if validation fails
        """
        request = await self._get_for_update(db, request_id, mdo_id)

        if not request:
            return None, "not_found"

        if request.status != ApprovalStatus.PENDING:
            return None, f"invalid_status:{request.status}"

        # Find the target item
        target_item = None
        for item in request.items:
            if item.id == item_id:
                target_item = item
                break

        if not target_item:
            return None, "item_not_found"

        if not target_item.cbp_plan_data:
            return None, "no_cbp_plan_data"

        # Remove the course with matching identifier from cbp_plan_data
        cbp_data = target_item.cbp_plan_data
        records = cbp_data if isinstance(cbp_data, list) else [cbp_data]

        found = False
        for record in records:
            courses = record.get("selected_courses", [])
            original_len = len(courses)
            record["selected_courses"] = [
                c for c in courses if c.get("identifier") != identifier
            ]
            if len(record["selected_courses"]) < original_len:
                found = True

        if not found:
            return None, "course_not_found"

        # Persist updated cbp_plan_data
        updated_data = records if isinstance(cbp_data, list) else records[0]
        await db.execute(
            update(ApprovalRequestItemRead)
            .where(ApprovalRequestItemRead.id == item_id)
            .values(cbp_plan_data=updated_data)
        )

        await db.commit()

        return {
            "item_id": str(item_id),
            "identifier": identifier,
        }, None

    async def update_item(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        item_id: uuid.UUID,
        mdo_id: str,
        update_data: dict,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Update role mapping fields on a specific approval request item.

        Returns:
            (result_dict, error_message)
            result_dict contains: item_id, fields_updated
            error_message is set if validation fails
        """
        request = await self._get_for_update(db, request_id, mdo_id)

        if not request:
            return None, "not_found"

        if request.status != ApprovalStatus.PENDING:
            return None, f"invalid_status:{request.status}"

        target_item = None
        for item in request.items:
            if item.id == item_id:
                target_item = item
                break

        if not target_item:
            return None, "item_not_found"

        if target_item.status != ApprovalStatus.PENDING:
            return None, "item_not_pending"

        if not update_data:
            return None, "no_fields_to_update"

        values = dict(update_data)

        # If designation_name is provided, also set igot_designation_name
        designation_name = values.get("designation_name", "").strip() if values.get("designation_name") else ""
        if designation_name:
            values["igot_designation_name"] = designation_name

        await db.execute(
            update(ApprovalRequestItemRead)
            .where(ApprovalRequestItemRead.id == item_id)
            .values(**values)
        )

        await db.commit()

        return {
            "item_id": str(item_id),
            "fields_updated": list(values.keys()),
        }, None

    async def get_failed_item_for_retry(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        item_id: uuid.UUID,
        mdo_id: str,
    ) -> Tuple[Optional[MdoApproval], Optional[ApprovalRequestItemRead]]:
        """
        Fetch the MdoApproval record and the FAILED item for retry publishing.
        Returns (mdo_approval, item) or (None, None) if not found.
        """
        # Get the MdoApproval record (plan_name, due_date)
        mdo_stmt = (
            select(MdoApproval)
            .where(
                and_(
                    MdoApproval.approval_request_id == request_id,
                    MdoApproval.approval_request_item_id == item_id,
                    MdoApproval.igot_cbp_plan_id.is_(None),
                )
            )
        )
        mdo_result = await db.execute(mdo_stmt)
        mdo_approval = mdo_result.scalars().first()

        if not mdo_approval:
            return None, None

        # Get the failed item directly
        item_stmt = (
            select(ApprovalRequestItemRead)
            .where(
                and_(
                    ApprovalRequestItemRead.id == item_id,
                    ApprovalRequestItemRead.approval_request_id == request_id,
                    ApprovalRequestItemRead.status == ApprovalItemStatus.FAILED,
                )
            )
        )
        item_result = await db.execute(item_stmt)
        item = item_result.scalars().first()

        return mdo_approval, item

    async def persist_retry_item_success(
        self,
        db: AsyncSession,
        mdo_approval_id: uuid.UUID,
        item_id: uuid.UUID,
        igot_cbp_plan_id: str,
    ) -> None:
        """Update the existing MdoApproval record and item status on successful retry."""
        now = datetime.now(timezone.utc)

        await db.execute(
            update(MdoApproval)
            .where(MdoApproval.id == mdo_approval_id)
            .values(
                igot_cbp_plan_id=uuid.UUID(igot_cbp_plan_id),
                created_at=now,
            )
        )
        await db.execute(
            update(ApprovalRequestItemRead)
            .where(ApprovalRequestItemRead.id == item_id)
            .values(status=ApprovalItemStatus.APPROVED)
        )
        await db.commit()


crud_mdo_approval_request = CRUDMDOApprovalRequest()