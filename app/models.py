from pydantic import BaseModel, Field
from typing import Optional, Any
from uuid import UUID
from datetime import datetime


# ---- FB Account ----

class FBAccountCreate(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    password: Optional[str] = None
    proxy: Optional[str] = None
    notes: Optional[str] = None
    session_data: Optional[Any] = None

    @property
    def login_identifier(self) -> str:
        """Return phone if provided, else email."""
        return self.phone or self.email or ""


class FBAccountUpdate(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    password: Optional[str] = None
    proxy: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    cookies: Optional[str] = None


class FBAccount(BaseModel):
    id: UUID
    email: str
    password: str
    proxy: Optional[str]
    status: str
    warmup_level: int
    last_used_at: Optional[datetime]
    notes: Optional[str]
    created_at: datetime


class ImportSessionCreateRequest(BaseModel):
    display_name: Optional[str] = None
    facebook_user_id: Optional[str] = None
    profile_url: Optional[str] = None
    verification_status: bool = False
    last_verified_at: Optional[str] = None
    session: Optional[dict[str, Any]] = None


class ImportSessionCreateResponse(BaseModel):
    id: Optional[str] = None
    display_name: Optional[str] = None
    facebook_user_id: Optional[str] = None
    profile_url: Optional[str] = None
    verification_status: bool = False
    last_verified_at: Optional[str] = None
    status: str = "active"
    message: str = "Import session account created successfully"


class ImportSessionRequest(BaseModel):
    account_name: Optional[str] = None
    session_data: Optional[str] = None


class ImportSessionResponse(BaseModel):
    verified: bool
    success: bool
    message: str
    profile: Optional[dict[str, Any]] = None


class ImportSessionValidation(BaseModel):
    required_fields: list[str] = ["session_data"]
    optional_fields: list[str] = ["account_name"]


# ---- Listing ----

class ListingCreate(BaseModel):
    account_id: Optional[UUID] = None
    title: str
    description: Optional[str] = None
    price: int = Field(default=0, description="Price in USD cents")
    category: Optional[str] = None
    condition: str = "used_good"
    images: list[str] = []


class ListingUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[int] = None
    category: Optional[str] = None
    condition: Optional[str] = None
    images: Optional[list[str]] = None
    status: Optional[str] = None
    fb_listing_id: Optional[str] = None


class Listing(BaseModel):
    id: UUID
    account_id: Optional[UUID]
    title: str
    description: Optional[str]
    price: int
    category: Optional[str]
    condition: str
    images: list[str]
    status: str
    fb_listing_id: Optional[str]
    published_at: Optional[datetime]
    created_at: datetime


# ---- Task ----

class TaskCreate(BaseModel):
    type: str
    input: dict[str, Any] = {}


class Task(BaseModel):
    id: UUID
    type: str
    status: str
    input: dict[str, Any]
    result: Optional[dict[str, Any]]
    progress: int
    total_steps: int
    completed_steps: int
    error: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    created_at: datetime


# ---- Log ----

class AutomationLog(BaseModel):
    id: UUID
    task_id: Optional[UUID]
    account_id: Optional[UUID]
    action: str
    status: str
    details: dict[str, Any]
    error: Optional[str]
    created_at: datetime


# ---- Automation Request Models ----

class NewAccountSlowRequest(BaseModel):
    account_id: str
    listing_count: int = Field(default=5, ge=1, le=50)
    delay_seconds: int = Field(default=30, ge=5, le=300)
    use_ai: bool = False
    product_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    condition: str = "used_good"
    price: int = 0
    images: list[str] = []


class NewAccountSlowV2Request(NewAccountSlowRequest):
    warmup_before: bool = True
    warmup_steps: int = Field(default=3, ge=1, le=10)


class UltraAIListingRequest(BaseModel):
    account_id: str
    listing_count: int = Field(default=10, ge=1, le=100)
    product_name: str
    category: str
    condition: str = "used_good"
    price: int = 0
    images: list[str] = []
    extra_details: str = ""


class CreateDraftsRequest(BaseModel):
    account_id: str
    draft_count: int = Field(default=5, ge=1, le=100)
    title: str
    description: Optional[str] = None
    price: int = 0
    category: Optional[str] = None
    condition: str = "used_good"
    images: list[str] = []
    use_ai: bool = False


class RenewListingsRequest(BaseModel):
    account_id: str
    listing_ids: Optional[list[str]] = None
    max_renew: int = Field(default=10, ge=1, le=200)
    delay_seconds: int = Field(default=10, ge=2, le=120)


class RelistListingsRequest(BaseModel):
    account_id: str
    listing_ids: Optional[list[str]] = None
    max_relist: int = Field(default=10, ge=1, le=200)
    delay_seconds: int = Field(default=10, ge=2, le=120)


class DraftPublisherAIRequest(BaseModel):
    account_id: str
    draft_ids: Optional[list[str]] = None
    max_publish: int = Field(default=10, ge=1, le=200)
    delay_seconds: int = Field(default=15, ge=5, le=300)
    improve_with_ai: bool = True


class DeleteAllListingsRequest(BaseModel):
    account_id: str
    status_filter: Optional[str] = None
    confirm: bool = Field(default=False, description="Must be true to execute")


class DraftPublisherRequest(BaseModel):
    account_id: str
    draft_ids: Optional[list[str]] = None
    max_publish: int = Field(default=10, ge=1, le=200)
    delay_seconds: int = Field(default=15, ge=5, le=300)


class PublishListingRequest(BaseModel):
    account_id: str
    listing_id: str
    delay_seconds: int = Field(default=10, ge=2, le=120)


class DeleteListingRequest(BaseModel):
    account_id: str
    listing_id: str


class DraftDeleteRequest(BaseModel):
    account_id: str
    draft_ids: Optional[list[str]] = None
    max_delete: int = Field(default=50, ge=1, le=500)
    confirm: bool = Field(default=False, description="Must be true to execute")


class AdsMultiplierRequest(BaseModel):
    account_id: str
    listing_ids: Optional[list[str]] = None
    multiplier: int = Field(default=2, ge=2, le=10)
    delay_seconds: int = Field(default=20, ge=5, le=300)


class WarmupRequest(BaseModel):
    account_id: str
    duration_minutes: int = Field(default=10, ge=1, le=60)
    actions_per_minute: int = Field(default=3, ge=1, le=10)


class ProfileUpdaterRequest(BaseModel):
    account_id: str
    # Basic profile
    name: Optional[str] = None          # display name (nickname/pronunciation)
    bio: Optional[str] = None           # intro/bio text
    location: Optional[str] = None      # current city
    hometown: Optional[str] = None      # hometown
    # Work
    workplace: Optional[str] = None     # company/employer name
    job_title: Optional[str] = None     # position/title
    # Education
    school: Optional[str] = None        # school/university name
    # Images (base64 or URL — currently not supported, kept for future)
    profile_pic_url: Optional[str] = None
    cover_pic_url: Optional[str] = None


class GetClicksRequest(BaseModel):
    account_id: str
    listing_ids: Optional[list[str]] = None


class OpenAccountRequest(BaseModel):
    account_ids: list[str]
    action: str = Field(
        default="verify",
        description="verify | check_status",
    )


class ListingAutomationRequest(BaseModel):
    account_id: str
    workflow_type: str = Field(
        default="renew",
        description="renew | relist | delete_and_repost | schedule",
    )
    listing_ids: Optional[list[str]] = None
    max_listings: int = Field(default=10, ge=1, le=200)
    delay_seconds: int = Field(default=30, ge=5, le=600)
    schedule_time: Optional[str] = None  # ISO format datetime
    repeat_interval: Optional[str] = None  # "daily", "weekly", "monthly"
    repeat_until: Optional[str] = None  # ISO format datetime


# ---- Inbox ----

class InboxReadRequest(BaseModel):
    account_id: UUID
    max_messages: int = Field(default=50, ge=1, le=200)


class InboxAutoReplyRequest(BaseModel):
    account_id: UUID
    message_ids: Optional[list[str]] = None
    max_replies: int = Field(default=20, ge=1, le=200)
    tone: str = Field(default="friendly", description="friendly | professional | casual | enthusiastic")
    custom_instructions: str = ""
    delay_seconds: int = Field(default=15, ge=5, le=300)


class InboxMessageUpdate(BaseModel):
    reply_text: str
