"""
Pydantic schemas for Designation Approval endpoints (SPV Admin flow).
"""
from datetime import datetime
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict, field_validator


# Request schemas
class ApproveDesignationBody(BaseModel):
    """Request body for approving a designation approval"""
    id: UUID = Field(..., description="Designation approval ID to approve")

class RejectDesignationBody(BaseModel):
    """Request body for rejecting a designation approval"""
    id: UUID = Field(..., description="Designation approval ID to reject")
    reviewer_comments: Optional[str] = Field(
        None,
        max_length=500,
        description="Reason for rejection (optional)"
    )


# Response schemas
class DesignationApprovalItem(BaseModel):
    """Schema for a single designation approval record in the SPV list view"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    designation_name: str
    organisation: Optional[str] = None
    organization_id: Optional[str] = None
    division: Optional[str] = None       # mapped from wing_division_section
    email: Optional[str] = None          # from user.email
    status: str
    reviewer_comments: Optional[str] = None
    created_at: datetime

    @classmethod
    def model_validate(cls, obj, **kwargs):
        data = {
            "id": obj.id,
            "designation_name": obj.designation_name,
            "organisation": getattr(obj, 'organisation', None),
            "organization_id": getattr(obj, 'organization_id', None),
            "division": obj.wing_division_section,
            "email": obj.user.email if obj.user else None,
            "status": obj.status,
            "reviewer_comments": obj.reviewer_comments,
            "created_at": obj.created_at,
        }
        return cls(**data)


class PaginationMetadata(BaseModel):
    """Pagination metadata"""
    current_page: int
    page_size: int
    total_items: int


class PaginatedDesignationApprovalResponse(BaseModel):
    """Paginated response for designation approvals"""
    items: List[DesignationApprovalItem]
    pagination: PaginationMetadata


class DesignationApprovalActionResponse(BaseModel):
    """Response after approve/reject action"""
    message: str
    status: str
    id: UUID
    designation_id: Optional[str] = None
