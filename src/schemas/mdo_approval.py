"""
Pydantic schemas for MDO approval endpoints.
"""
from datetime import datetime
from typing import Any, Optional, List, Union
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict, field_validator


def _validate_rejection_comment(v: str) -> str:
    if not v or not v.strip():
        raise ValueError('Rejection comment cannot be empty')
    return v.strip()


# Request schemas
class ApproveRequestBody(BaseModel):
    """Request body for approving designations"""
    request_id: UUID = Field(..., description="ID of the approval request")
    plan_name: str = Field(..., description="Name of the CBP plan")
    due_date: datetime = Field(..., description="Due date for plan completion")


class RetryPublishItemBody(BaseModel):
    """Request body for retrying publish of a single failed item"""
    request_id: UUID = Field(..., description="ID of the approval request")
    item_id: UUID = Field(..., description="ID of the failed item to retry")


class RejectRequestBody(BaseModel):
    """Request body for rejecting designations"""
    request_id: UUID = Field(..., description="ID of the approval request")
    rejection_comment: str = Field(
        ..., 
        min_length=1,
        max_length=500,
        description="Reason for rejection (required, maximum 500 characters)"
    )

    @field_validator('rejection_comment')
    @classmethod
    def validate_rejection_comment(cls, v: str) -> str:
        return _validate_rejection_comment(v)


class RejectItemBody(BaseModel):
    """Request body for rejecting individual item"""
    request_id: UUID = Field(..., description="ID of the approval request")
    item_id: UUID = Field(..., description="ID of the specific item to reject")
    rejection_comment: str = Field(
        ..., 
        min_length=1,
        max_length=500,
        description="Reason for rejecting this specific item (required, maximum 500 characters)"
    )

    @field_validator('rejection_comment')
    @classmethod
    def validate_rejection_comment(cls, v: str) -> str:
        return _validate_rejection_comment(v)

class AddCourseBody(BaseModel):
    request_id: UUID = Field(...)
    item_id: UUID = Field(...)
    identifiers: List[str] = Field(..., min_length=1, description="List of course identifiers to add")


class RemoveCourseBody(BaseModel):
    """Request body for removing a course from an approval request item"""
    request_id: UUID = Field(..., description="ID of the approval request")
    item_id: UUID = Field(..., description="ID of the approval request item")
    identifier: str = Field(..., min_length=1, description="Course identifier to remove")


class CompetencyItem(BaseModel):
    """Schema for a single competency entry."""
    type: str = Field(..., description="Competency type")
    theme: str = Field(..., description="Competency theme")
    sub_theme: str = Field(..., description="Competency sub-theme")


class UpdateItemBody(BaseModel):
    """Request body for updating an approval request item's role mapping fields."""
    request_id: UUID = Field(..., description="ID of the approval request")
    item_id: UUID = Field(..., description="ID of the specific item to update")
    designation_name: Optional[str] = Field(None, description="Designation name")
    igot_designation_id: Optional[str] = Field(None, description="iGOT designation ID")
    wing_division_section: Optional[str] = Field(None, description="Wing/division/section name")
    role_responsibilities: Optional[list] = Field(None, description="Roles and responsibilities")
    activities: Optional[list] = Field(None, description="Activities list")
    competencies: Optional[List[CompetencyItem]] = Field(None, description="Competencies list")

# Response schemas
class UserInfo(BaseModel):
    """Schema for user info attached to approval requests"""
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    username: str
    email: str


class ApprovalRequestItemSchema(BaseModel):
    """Schema for individual approval request item (designation)"""
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    designation_name: str
    wing_division_section: Optional[str] = None
    role_responsibilities: Optional[Union[dict, list]] = None
    activities: Optional[Union[dict, list]] = None
    competencies: Optional[Union[dict, list]] = None
    igot_designation_name: Optional[str] = None
    igot_designation_id: Optional[str] = None
    cbp_plan_data: Optional[Union[dict, list]] = None
    status: Optional[str] = "pending"
    sort_order: Optional[int] = None
    reviewer_comments: Optional[str] = None
    rejected_at: Optional[datetime] = None


class ApprovalRequestListItem(BaseModel):
    """Schema for approval request in list view"""
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    request_name: str
    user_id: UUID
    user: Optional[UserInfo] = None
    created_at: datetime
    designation_count: int
    status: str
    state_center_name: str
    department_name: Optional[str] = None
    reviewer_comments: Optional[str] = None


class ApprovalRequestDetail(BaseModel):
    """Schema for detailed approval request view"""
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    request_name: str
    created_at: datetime
    designation_count: int
    status: str
    state_center_name: str
    department_name: Optional[str] = None
    org_type: Optional[str] = None
    state_center_id: str
    department_id: Optional[str] = None
    user_id: UUID
    user: Optional[UserInfo] = None
    rejected_at: Optional[datetime] = None
    reviewer_comments: Optional[str] = None
    items: List[ApprovalRequestItemSchema] = []


class PaginationMetadata(BaseModel):
    """Pagination metadata"""
    current_page: int
    page_size: int
    total_items: int


class ApprovalRequestFilters(BaseModel):
    """Applied filters"""
    search: Optional[str] = None
    status_filter: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None


class PaginatedApprovalRequestsResponse(BaseModel):
    """Paginated response for approval requests"""
    items: List[ApprovalRequestListItem]
    pagination: PaginationMetadata
    # filters: ApprovalRequestFilters


class ItemPublishResult(BaseModel):
    """Result of publishing a single item"""
    item_id: str
    designation_name: str
    status: str  # "success" or "failed"
    plan_id: Optional[str] = None
    error: Optional[str] = None


class ApprovalActionResponse(BaseModel):
    """Response after approve action"""
    message: str
    request_status: str
    items_processed: int
    items_succeeded: int
    items_failed: int
    results: List[ItemPublishResult]


class RejectActionResponse(BaseModel):
    """Response after reject action"""
    message: str
    request_status: str
    items_processed: int
    item_ids: List[UUID]
