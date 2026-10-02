"""Declarative card layouts: field references only, never executable templates."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')
class CardField(Strict):
    field: str = Field(min_length=1, max_length=120)
    label: str = Field(default='', max_length=120)
    format: Literal['text','markdown','number','currency','percent','date','badge'] = 'text'
    currency: str = Field(default='USD', pattern='^[A-Z]{3}$')
class CardSection(Strict):
    title: str = Field(default='', max_length=120)
    fields: list[CardField] = Field(max_length=40)
class CardLayout(Strict):
    title_field: str = Field(default='Name', max_length=120)
    sections: list[CardSection] = Field(max_length=12)
    show_narrative: bool = True
    show_related: bool = True

def visible_layout(layout, fields):
    """Project configuration through the same permitted field set as the record."""
    return {**layout, 'title_field':layout.get('title_field') if layout.get('title_field') in fields else '',
            'sections':[{**section,'fields':[f for f in section['fields'] if f['field'] in fields]}
                        for section in layout.get('sections',[])
                        if any(f['field'] in fields for f in section['fields'])]}
