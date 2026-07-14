from enum import StrEnum


class RunState(StrEnum):
    CREATED = "created"
    PREFLIGHT = "preflight"
    WARMING = "warming"
    MEASURING = "measuring"
    COLLECTING = "collecting"
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
