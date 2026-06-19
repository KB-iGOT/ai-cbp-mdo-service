"""
CRUD operations for Designation Approval (SPV Admin flow).
"""
import uuid
from datetime import datetime, date, timezone, time as dtime
from typing import List, Optional, Tuple

from sqlalchemy import and_, desc, func, or_, update, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from ..models.designation_approval import DesignationApproval, DesignationApprovalStatus
from ..models.user import User
from ..core.database import sessionmanager


class CRUDDesignationApproval:
    """
    CRUD methods for SPV Admin to manage designation approvals.
    """

    async def list_designation_approvals(
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
        Returns (items, total_count).
        """
        conditions = []

        # Search across designation_name, wing_division_section, email, state_center_name, and department_name
        if search:
            search_term = search.strip()
            # Subquery to find rolemapping_ids matching state_center_name or department_name
            org_subquery = text(
                "SELECT id FROM role_mappings WHERE state_center_name ILIKE :term OR department_name ILIKE :term"
            ).bindparams(term=f"%{search_term}%").columns(id=PG_UUID)
            conditions.append(
                or_(
                    DesignationApproval.designation_name.ilike(f"%{search_term}%"),
                    DesignationApproval.wing_division_section.ilike(f"%{search_term}%"),
                    User.email.ilike(f"%{search_term}%"),
                    DesignationApproval.rolemapping_id.in_(org_subquery),
                )
            )

        # Filter by org_id (matches either state_center_id or department_id in role_mappings)
        if org_id:
            org_subquery = text(
                "SELECT id FROM role_mappings WHERE state_center_id = :org_id OR department_id = :org_id"
            ).bindparams(org_id=org_id).columns(id=PG_UUID)
            conditions.append(
                DesignationApproval.rolemapping_id.in_(org_subquery)
            )

        if status_filter:
            normalized = status_filter.lower()
            conditions.append(DesignationApproval.status == normalized)

        if from_date:
            conditions.append(DesignationApproval.created_at >= datetime.combine(from_date, dtime.min))
        if to_date:
            conditions.append(DesignationApproval.created_at <= datetime.combine(to_date, dtime.max))

        where_clause = and_(*conditions) if conditions else True

        # Base query with LEFT JOIN on users for email search
        base_query = (
            select(DesignationApproval)
            .outerjoin(User, DesignationApproval.user_id == User.user_id)
            .where(where_clause)
        )

        # Count total
        count_stmt = select(func.count()).select_from(base_query.subquery())
        count_result = await db.execute(count_stmt)
        total = count_result.scalar_one()

        # Fetch page with user for email display
        offset = (page - 1) * page_size
        stmt = (
            base_query
            .options(selectinload(DesignationApproval.user))
            .order_by(desc(DesignationApproval.created_at))
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        rows = list(result.scalars().all())

        # Bulk-fetch organisation names from role_mappings in one query
        rolemapping_ids = list({r.rolemapping_id for r in rows if r.rolemapping_id}) 
        org_map: dict = {}
        if rolemapping_ids:
            org_result = await db.execute(
                text("SELECT id, state_center_name, department_name, department_id, state_center_id FROM role_mappings WHERE id = ANY(:ids)")
                .bindparams(ids=rolemapping_ids)
            )
            for rm_row in org_result:
                key = str(rm_row.id)
                org_map[key] = {
                    "name": rm_row.department_name if rm_row.department_name else rm_row.state_center_name,
                    "organization_id": str(rm_row.department_id) if rm_row.department_id else (str(rm_row.state_center_id) if rm_row.state_center_id else None),
                }

        # Attach organisation as a transient attribute
        for row in rows:
            org_data = org_map.get(str(row.rolemapping_id), {})
            row.organisation = org_data.get("name")
            row.organization_id = org_data.get("organization_id")

        return rows, total

    async def _get_pending_for_update(
        self,
        db: AsyncSession,
        record_id: uuid.UUID,
    ) -> Optional[DesignationApproval]:
        """
        Lock and fetch a single PENDING designation approval for update.
        Returns the record if PENDING, None otherwise.
        """
        stmt = (
            select(DesignationApproval)
            .where(
                and_(
                    DesignationApproval.id == record_id,
                    DesignationApproval.status == DesignationApprovalStatus.PENDING.value,
                )
            )
            .with_for_update()
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(
        self,
        record_id: uuid.UUID,
    ) -> Optional[DesignationApproval]:
        """Fetch a designation approval by ID with user relationship."""
        async with sessionmanager.session() as db:
            stmt = (
                select(DesignationApproval)
                .options(selectinload(DesignationApproval.user))
                .where(DesignationApproval.id == record_id)
            )
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def get_pending(
        self,
        db: AsyncSession,
        record_id: uuid.UUID,
    ) -> Optional[DesignationApproval]:
        """
        Fetch a PENDING designation approval by ID (no lock, read-only check).
        Returns the record if PENDING, None otherwise.
        """
        stmt = (
            select(DesignationApproval)
            .where(
                and_(
                    DesignationApproval.id == record_id,
                    DesignationApproval.status == DesignationApprovalStatus.PENDING.value,
                )
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def approve(
        self,
        db: AsyncSession,
        record_id: uuid.UUID,
    ) -> bool:
        """
        Approve a single designation approval by ID.
        Locks PENDING record, updates to APPROVED, and commits.
        Returns True if approved, False if not found or already processed.
        """
        record = await self._get_pending_for_update(db, record_id)
        if not record:
            return False

        now = datetime.now(timezone.utc)
        await db.execute(
            update(DesignationApproval)
            .where(DesignationApproval.id == record_id)
            .values(
                status=DesignationApprovalStatus.APPROVED.value,
                updated_at=now,
            )
        )
        await db.commit()
        return True

    async def reject(
        self,
        db: AsyncSession,
        record_id: uuid.UUID,
        reviewer_comments: Optional[str] = None,
    ) -> bool:
        """
        Reject a single designation approval by ID.
        Locks PENDING record, updates to REJECTED with comments.
        Returns True if rejected, False if not found or already processed.
        """
        record = await self._get_pending_for_update(db, record_id)
        if not record:
            return False

        now = datetime.now(timezone.utc)
        await db.execute(
            update(DesignationApproval)
            .where(DesignationApproval.id == record_id)
            .values(
                status=DesignationApprovalStatus.REJECTED.value,
                reviewer_comments=reviewer_comments,
                updated_at=now,
            )
        )
        await db.commit()
        return True


crud_designation_approval = CRUDDesignationApproval()
