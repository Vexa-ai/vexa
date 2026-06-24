from admin_models.models import Base as AdminBase, User, VEXA_SCHEMA as ADMIN_SCHEMA
from meeting_api.models import Base as MeetingBase, Meeting, VEXA_SCHEMA as MEETING_SCHEMA


def test_vexa_models_use_explicit_vexa_schema():
    assert ADMIN_SCHEMA == "vexa"
    assert MEETING_SCHEMA == "vexa"
    assert AdminBase.metadata.schema == "vexa"
    assert MeetingBase.metadata.schema == "vexa"
    assert User.__table__.schema == "vexa"
    assert Meeting.__table__.schema == "vexa"
