"""
HỢP ĐỒNG DỮ LIỆU giữa các module.
"""
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Intent(str, Enum):
    SALES = "SALES"
    APPOINTMENT = "APPOINTMENT"
    KNOWLEDGE = "KNOWLEDGE"
    CONTRACT = "CONTRACT"
    DISCOUNT = "DISCOUNT"
    MAINTENANCE = "MAINTENANCE"
    AFTER_SALES = "AFTER_SALES"
    GREETING = "GREETING"
    OTHER = "OTHER"


class ConversationMode(str, Enum):
    BOT = "BOT"
    HUMAN_PENDING = "HUMAN_PENDING"
    HUMAN_ACTIVE = "HUMAN_ACTIVE"


class ServiceType(str, Enum):
    MAINTENANCE = "MAINTENANCE"
    REPAIR = "REPAIR"
    TEST_DRIVE = "TEST_DRIVE"
    SHOWROOM_VISIT = "SHOWROOM_VISIT"


# ---- API ----
class ChatRequest(BaseModel):
    # UUID trình duyệt tạo và giữ trong localStorage = chat_sessions.id. Chỉ nhận ra phiên, không chứng minh danh tính.
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    mode: ConversationMode
    message_id: int | None = None  # id tin bot vừa gửi (client dùng để poll tin nhân viên/hệ thống sau đó)
    trace: list[str] = []  # các node đã đi qua - dùng để debug và để chấm eval theo trajectory


# ---- Dữ liệu trả về từ các service ----
class Product(BaseModel):
    id: int
    kind: str  # CAR | PART
    name: str
    list_price_vnd: int
    price_as_of: str  # ngày hiệu lực của giá, bắt buộc phải hiển thị khi báo giá
    specs: dict = {}


class ServiceRecord(BaseModel):
    id: int
    service_date: str
    service_type: str
    description: str
    cost_vnd: int


class KnowledgeChunk(BaseModel):
    text: str
    source: str
    score: float


class BookingResult(BaseModel):
    ok: bool
    appointment_id: int | None = None
    status: str = ""  # CONFIRMED | PENDING_APPROVAL | REJECTED
    message: str = ""


class HandoverResult(BaseModel):
    task_id: int


class MaintenanceResult(BaseModel):
    """Kết quả tra cứu bảo dưỡng theo mốc km."""
    km_milestone: int
    model_name: str
    tasks: list[str]
    estimated_cost: float


# ---- New Conversation Interpretation Architecture ----

class TaskRelationship(str, Enum):
    """How the current task relates to conversation context."""
    NEW_REQUEST = "NEW_REQUEST"  # Explicit new request
    ANSWER_TO_QUESTION = "ANSWER_TO_QUESTION"  # Answers a pending question
    ACCEPT_OFFER = "ACCEPT_OFFER"  # Accepts a pending offer
    REJECT_OFFER = "REJECT_OFFER"  # Rejects a pending offer
    CORRECT_PREVIOUS_INFO = "CORRECT_PREVIOUS_INFO"  # Corrects earlier information
    CONTINUE_TASK = "CONTINUE_TASK"  # Continues an active workflow
    CHANGE_TOPIC = "CHANGE_TOPIC"  # Switches to a different topic
    CANCEL_TASK = "CANCEL_TASK"  # Cancels active workflow or pending task
    UNCLEAR = "UNCLEAR"  # Ambiguous; requires clarification


class SlotOperation(str, Enum):
    """Type of slot update operation."""
    SET = "SET"  # Set a new value
    CORRECT = "CORRECT"  # Correct an existing value
    CLEAR = "CLEAR"  # Remove the value


class FactSource(str, Enum):
    """Origin of a known fact."""
    USER_EXPLICIT = "USER_EXPLICIT"  # User stated directly
    USER_CORRECTED = "USER_CORRECTED"  # User corrected previous value
    APPLICATION_VERIFIED = "APPLICATION_VERIFIED"  # Verified by app logic
    INFERRED = "INFERRED"  # Inferred from context


class InteractionKind(str, Enum):
    """Type of pending interaction."""
    QUESTION = "QUESTION"  # Bot asked a question
    OFFER = "OFFER"  # Bot offered an action
    CONFIRMATION = "CONFIRMATION"  # Bot requested confirmation


class InteractionStatus(str, Enum):
    """Lifecycle status of a pending interaction."""
    OPEN = "OPEN"  # Awaiting response
    RESOLVED = "RESOLVED"  # Response received and processed
    REJECTED = "REJECTED"  # User declined
    EXPIRED = "EXPIRED"  # No longer relevant


class WorkflowStatus(str, Enum):
    """Status of active workflow."""
    NONE = "NONE"  # No active workflow
    ACTIVE = "ACTIVE"  # Collecting information
    WAITING_FOR_USER = "WAITING_FOR_USER"  # Waiting for user input
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"  # Awaiting approval


class SlotUpdate(BaseModel):
    """A proposed update to a known fact/slot."""
    key: str = Field(description="Canonical fact name")
    operation: SlotOperation = Field(description="Type of update: SET, CORRECT, or CLEAR")
    value: Optional[Any] = Field(default=None, description="New value (null for CLEAR)")
    evidence: str = Field(description="Quoted text from user message supporting this update")
    source_message_id: Optional[int] = Field(default=None, description="Message ID where this update originated")
    related_task_id: Optional[str] = Field(default=None, description="Task ID this update relates to")


class PolicySignals(BaseModel):
    """Policy-relevant facts about the current message, read by policy_guard instead of keywords."""
    wants_human: bool = Field(default=False, description="Customer explicitly asks for a human/staff member")
    complaint: bool = Field(default=False, description="Customer complains or is clearly dissatisfied")
    safety_risk: bool = Field(default=False, description="Vehicle shows a dangerous symptom (brakes, fire, smoke, steering)")
    discount_request: bool = Field(default=False, description="Customer asks for or bargains a discount/gift")
    deal_change: bool = Field(default=False, description=(
        "Customer asks to change contract terms, renegotiate the price, or asks about contract fees; "
        "not set when merely answering the decided price"))
    note: Optional[str] = Field(default=None, description="Short reason for any true signal")


class EntityReference(BaseModel):
    """A reference such as 'xe số 1', 'mẫu thứ ba', 'nó' in the current message."""
    mention: str = Field(description="Verbatim reference text from the current message")
    position: Optional[int] = Field(
        default=None, description="1-based position in shown_products; -1 means the last one")
    entity_id: Optional[int] = Field(default=None, description="product id from shown_products, never invented")
    ambiguous: bool = Field(default=False, description="True when the reference cannot be resolved uniquely")


class TaskInterpretation(BaseModel):
    """Interpretation of a single task within the user's message."""
    task_id: str = Field(description="Application-generated unique ID for this task in this turn")
    domain: Intent = Field(description="Domain: SALES, APPOINTMENT, KNOWLEDGE, etc.")
    action: str = Field(description="Canonical action name from available_actions")
    text: str = Field(description="Relevant excerpt from user message for this task")
    relationship: TaskRelationship = Field(description="How this task relates to conversation context")
    referenced_interaction_id: Optional[str] = Field(
        default=None, description="ID of the pending interaction this task responds to")
    requires_clarification: bool = Field(
        default=False, description="True when task cannot proceed without clarification")
    entity_id: Optional[int] = Field(
        default=None, description="Resolved catalog/entity identifier for this task")
    entity_type: Optional[str] = Field(
        default=None, description="Resolved entity type, e.g. PRODUCT")
    inquiry_only: bool = Field(
        default=False, description="Asks about a service/procedure without asking to start that workflow")


class ConversationInterpretation(BaseModel):
    """Structured interpretation of the current user message."""
    message_summary: str = Field(description="Concise summary of user's request")
    tasks: list[TaskInterpretation] = Field(
        default_factory=list, description="Deduplicated, ordered task interpretations")
    slot_updates: list[SlotUpdate] = Field(
        default_factory=list, description="Proposed fact/slot changes with explicit operations")
    references: list[EntityReference] = Field(
        default_factory=list, description="References to previously shown products")
    signals: PolicySignals = Field(default_factory=PolicySignals)
    # Interpretation signals consumed by application policy; these are not duplicated task state.
    complexity: str = Field(default="LOW", description="LOW or HIGH complexity of this turn")
    accepts_offer: Optional[bool] = Field(default=None)
    uncertain: bool = Field(default=False)


class KnownFact(BaseModel):
    """A fact known about the customer or conversation."""
    key: str = Field(description="Canonical fact name")
    value: Any = Field(description="Fact value (typed)")
    source: FactSource = Field(description="Origin of this fact")
    updated_at_message_id: Optional[int] = Field(
        default=None, description="Message ID of the last update")
    confidence: Optional[float] = Field(
        default=None, description="Confidence score for inferred facts (0.0-1.0)")


class PendingInteraction(BaseModel):
    """A question, offer, or confirmation awaiting user response."""
    interaction_id: str = Field(description="Stable application-generated ID")
    kind: InteractionKind = Field(description="Type: QUESTION, OFFER, or CONFIRMATION")
    text: str = Field(description="The question/offer presented to the user")
    status: InteractionStatus = Field(description="Lifecycle status")
    related_task_id: Optional[str] = Field(default=None, description="Associated task ID if any")
    created_at_message_id: Optional[int] = Field(default=None, description="Message ID when this interaction was created")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional context (e.g., asked field name)")


class ActiveWorkflow(BaseModel):
    """Currently active multi-turn workflow."""
    workflow_id: str = Field(description="Stable workflow identifier")
    workflow_type: str = Field(description="Type: 'appointment', 'contract', etc.")
    status: WorkflowStatus = Field(description="Current workflow status")
    collected_facts: dict[str, Any] = Field(
        default_factory=dict, description="Facts collected during this workflow")


class ConversationContext(BaseModel):
    """Complete context passed to the conversation interpreter."""
    schema_version: str = Field(default="1.0", description="Version of this context schema")
    current_message: dict = Field(description="Current user message: {id, text, timestamp}")
    history: list[dict] = Field(
        default_factory=list, description="Chronological conversation history")
    known_facts: dict[str, KnownFact] = Field(
        default_factory=dict, description="Facts known about the customer")
    pending_interactions: list[PendingInteraction] = Field(
        default_factory=list, description="Open questions/offers awaiting response")
    active_workflow: Optional[ActiveWorkflow] = Field(
        default=None, description="Currently active workflow if any")
    selected_entities: list[dict] = Field(
        default_factory=list, description="Resolved entities (products, bookings) from prior turns")
    shown_products: list[dict] = Field(
        default_factory=list, description="Most recent product list shown by the bot, with 1-based positions")
    canonical_slots: dict[str, str] = Field(
        default_factory=dict, description="The only slot keys allowed in slot_updates")
    available_actions: list[dict] = Field(
        default_factory=list, description="Canonical actions the application permits")
