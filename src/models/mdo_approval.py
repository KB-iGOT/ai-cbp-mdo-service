"""
MDO Approval models - mirrors approval_requests, approval_request_items, and mdo_approval tables.
This service has READ access to approval_requests/items and FULL WRITE access to mdo_approval.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Enum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from ..core.database import Base
from ..schemas.comman import ApprovalStatus, ApprovalItemStatus


class ApprovalRequestRead(Base):
    """
    READ-ONLY mirror of approval_requests table from cbp-tool.
    Used by MDO portal to view pending approval requests.
    """
    __tablename__ = "approval_requests"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_name = Column(String(100), nullable=False, index=True)

    # Who submitted
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Organization context
    org_type = Column(
        String(20),
        nullable=True,
        index=True,
        comment="Organization type: ministry or state"
    )
    state_center_id = Column(String(255), nullable=False, index=True)
    department_id = Column(String(255), nullable=True, index=True)
    state_center_name = Column(String(255), nullable=False)
    department_name = Column(String(255), nullable=True)

    # MDO who should approve
    mdo_id = Column(String(255), nullable=False, index=True)

    # Counts
    designation_count = Column(Integer, nullable=False, default=0)

    # Status - uses enum from cbp-tool
    status = Column(
        Enum(ApprovalStatus, name="approval_status_enum", create_type=True),
        nullable=False,
        default=ApprovalStatus.PENDING
    )

    # Timestamps
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Reviewer comments
    reviewer_comments = Column(Text, nullable=True)

    # Relationships
    items = relationship(
        "ApprovalRequestItemRead",
        back_populates="approval_request",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    user = relationship("User", foreign_keys=[user_id], lazy="noload")


class ApprovalRequestItemRead(Base):
    """
    READ-ONLY mirror of approval_request_items table from cbp-tool.
    Used by MDO portal to view individual designations in approval requests.
    """
    __tablename__ = "approval_request_items"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    approval_request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("approval_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Source reference
    source_role_mapping_id = Column(UUID(as_uuid=True), nullable=False)

    # Designation details
    designation_name = Column(String(255), nullable=False, index=True)
    wing_division_section = Column(String(255), nullable=True)

    # JSONB fields
    role_responsibilities = Column(JSONB, default=list, nullable=True)
    activities = Column(JSONB, default=list, nullable=True)
    competencies = Column(JSONB, default=list, nullable=True)

    sort_order = Column(
        Integer,
        nullable=True,
        index=True,
        comment="Sort order for hierarchical arrangement of designations"
    )

    # iGOT portal fields
    igot_designation_name = Column(
        String(255),
        nullable=True,
        comment="Designation name as it exists in the iGOT portal"
    )
    igot_designation_id = Column(
        String(255),
        nullable=True,
        comment="Designation ID from the iGOT portal"
    )

    # CBP plan snapshot
    cbp_plan_data = Column(JSONB, nullable=True)

    # Item status
    status = Column(
        Enum(ApprovalItemStatus, name="approval_request_item_status_enum", create_type=True),
        nullable=False,
        default=ApprovalItemStatus.PENDING
    )

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Reviewer comments and rejection timestamp
    reviewer_comments = Column(Text, nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)

    # Relationship back to request
    approval_request = relationship("ApprovalRequestRead", back_populates="items")


class MdoApproval(Base):
    """
    FULL WRITE ACCESS - This service owns this table.
    Tracks MDO approval/rejection actions on individual designation items.
    """
    __tablename__ = "mdo_approval"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # References
    approval_request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("approval_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    approval_request_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("approval_request_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Plan details
    plan_name = Column(String(200), nullable=False, index=True)
    due_date = Column(DateTime(timezone=True), nullable=False, index=True)

    # iGOT CBP Plan ID for tracking published approvals
    igot_cbp_plan_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    # Timestamp when igot_cbp_plan_id was created
    created_at = Column(DateTime(timezone=True), nullable=True)