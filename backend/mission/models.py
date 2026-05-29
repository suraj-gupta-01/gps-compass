from pydantic import BaseModel
from typing import List, Literal, Union, Optional

class LatLng(BaseModel):
    lat: float
    lng: float

class GCSModel(BaseModel):
    id: str
    position: LatLng

class WaypointPathSegment(BaseModel):
    type: Literal['waypoint_path']
    id: str
    label: str
    points: List[LatLng]

class AreaCoverageSegment(BaseModel):
    type: Literal['area_coverage']
    id: str
    label: str
    sweepWidth: float
    polygon: List[LatLng]

# Pydantic v2 discriminated union
from pydantic import RootModel
from typing import Annotated
from pydantic import Field

Segment = Annotated[
    Union[WaypointPathSegment, AreaCoverageSegment],
    Field(discriminator='type')
]

class MissionJSON(BaseModel):
    groundControl: Optional[GCSModel] = None
    mission: List[Segment] = []