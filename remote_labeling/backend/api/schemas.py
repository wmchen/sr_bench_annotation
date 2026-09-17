"""Version-one request contracts; source for generated frontend types."""

from __future__ import annotations

from typing import Literal

from pydantic import (
    StrictInt,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    FiniteFloat,
)


class RequestModel(BaseModel):
    """Reject unknown API control fields."""

    model_config = ConfigDict(extra="forbid")


class Exchange(RequestModel):
    """Capability exchange; tokens are never placed in query strings."""

    token: str = Field(min_length=20, max_length=256)
    nickname: str = Field(default="访客", max_length=80)


class LeaseRequest(RequestModel):
    """Bind editing ownership to a fresh browser-tab identifier."""

    tab_id: str = Field(min_length=16, max_length=128)
    lease_id: str | None = None


class WriteRequest(LeaseRequest):
    """Concurrency fence present on every annotation mutation."""

    lease_id: str = Field(min_length=16, max_length=128)
    lease_generation: str = Field(min_length=16, max_length=128)
    base_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=16, max_length=128)


class DraftRequest(WriteRequest):
    """HR geometry/text and per-variant recoverability are the writable data."""

    hr: list[dict[str, JsonValue]] = Field(max_length=5000)
    recoverability: dict[str, dict[str, StrictInt | None]] = Field(
        default_factory=dict
    )


class CommitRequest(WriteRequest):
    """Explicit acknowledgements distinguish warnings from hard validation."""

    confirm_empty: bool = False
    confirm_monotonic: bool = False


class SaveAnnotationsRequest(CommitRequest):
    """Fence publication against both the online draft and source files."""

    source_token: str = Field(min_length=64, max_length=64)
    image_version: str = Field(min_length=64, max_length=64)


class ShareRequest(RequestModel):
    """Owner-defined authorization scope independent of the current page."""

    role: Literal["edit", "view"]
    dataset: str
    sample: str | None = None
    expires: FiniteFloat | None = None


class SlotRequest(RequestModel):
    """Compare-and-swap control for the service-wide model slot."""

    generation: int = Field(ge=0)
    model_id: str | None = None
    device: str = "auto"


class InferenceRequest(WriteRequest):
    """An inference request is pinned to source, model and annotation versions."""

    dataset: str
    sample: str
    image_version: str
    model_id: str
    model_version: str
    slot_generation: int
    region_ids: list[str] = Field(default_factory=list, max_length=5000)


class ExportRequest(RequestModel):
    """Freeze currently confirmed results for one dataset."""

    dataset: str


class RegionView(BaseModel):
    """Canonical region with lossless compatibility extension fields."""

    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    region_id: str
    label: Literal["text", "face"]
    description: str
    shape_type: Literal["rectangle", "quadrilateral"]
    points: list[tuple[FiniteFloat, FiniteFloat]]
    recoverable: Literal[0, 1, 2] | None


class GroupView(BaseModel):
    """One transaction's synchronized four-variant annotation group."""

    HR: list[RegionView]
    LR2: list[RegionView]
    LR3: list[RegionView]
    LR4: list[RegionView]


class DimensionsView(BaseModel):
    """Actual image dimensions used for coordinate projection."""

    HR: tuple[int, int]
    LR2: tuple[int, int]
    LR3: tuple[int, int]
    LR4: tuple[int, int]


class MissingValue(BaseModel):
    """Locate the evidence entry that prevents a formal commit."""

    variant: Literal["HR", "LR2", "LR3", "LR4"]
    region_id: str


class ValidationView(BaseModel):
    """Hard completeness failures and advisory monotonicity findings."""

    missing: list[MissingValue]
    violations: list[str]
    empty: bool


class OccupancyView(BaseModel):
    """Public lease status without writable lease credentials."""

    nickname: str
    expires: float


class VariantStatisticsView(BaseModel):
    """Evidence counts for one image variant."""

    sufficient: int = Field(ge=0)
    ambiguous: int = Field(ge=0)
    insufficient: int = Field(ge=0)
    unset: int = Field(ge=0)
    assigned: int = Field(ge=0)
    total: int = Field(ge=0)


class DatasetStatisticsView(BaseModel):
    """Saved draft counts and formal progress in an authorized scope."""

    dataset: str
    attribute: Literal["text", "face"]
    status: Literal["ready", "scanning", "invalid"]
    scope: Literal["dataset", "sample"]
    import_version: int
    generated_at: float
    sample_groups: int = Field(ge=0)
    image_files: int = Field(ge=0)
    instances: int = Field(ge=0)
    completed_instances: int = Field(ge=0)
    recoverability_assigned: int = Field(ge=0)
    recoverability_total: int = Field(ge=0)
    complete_samples: int = Field(ge=0)
    pending_samples: int = Field(ge=0)
    by_variant: dict[Literal["HR", "LR2", "LR3", "LR4"], VariantStatisticsView]


class OpeningSelectionView(BaseModel):
    """Authorized opening target and its index in the unfiltered list."""

    sample: str | None
    index: int | None
    pending_draft: bool


class SampleView(BaseModel):
    """Public saved state; private source paths never cross this interface."""

    dataset: str
    id: str
    attribute: Literal["text", "face"]
    revision: int
    committed_revision: int | None
    draft: GroupView
    formal: GroupView | None
    complete: bool
    dimensions: DimensionsView
    image_version: str
    validation: ValidationView
    occupancy: OccupancyView | None = None
    source_group: GroupView | None = None
    source_token: str = ""
    source_ready: bool = False
    source_dirty: bool = True
    source_error: str | None = None


class SessionView(BaseModel):
    """Effective current permission and timing values."""

    session_id: str
    role: Literal["owner", "edit", "view"]
    nickname: str
    dataset: str | None
    sample: str | None
    lease_seconds: int
    heartbeat_seconds: int


class DownloadProgress(BaseModel):
    """Public transfer progress, without private filesystem paths or URLs."""

    model_id: str
    component: str
    filename: str
    file_index: int
    files_total: int
    bytes_received: int
    total_bytes: int | None
    attempt: int
    stage: str


class ModelView(BaseModel):
    """Distinguish installed models from supported automatic downloads."""

    id: str
    kind: str
    attribute: str
    version: str
    available: bool
    downloadable: bool
    errors: list[str]
    required_memory_mb: int


class SlotView(BaseModel):
    """Observable shared model slot; requests use generation as a fence."""

    state: str
    generation: int
    model_id: str | None
    model_version: str | None
    device: str | None
    queued: int
    error: str | None
    running_job: str | None
    providers: list[dict[str, JsonValue]]
    download: DownloadProgress | None = None


class JobView(BaseModel):
    """Session-scoped task state and optional measured pipeline stages."""

    id: str
    dataset: str
    sample: str
    state: str
    error: str | None
    created: float
    updated: float
    timings: dict[str, float] = Field(default_factory=dict)
