from pydantic import BaseModel

from app.models.notebook import Notebook
from app.models.notebook_data import NotebookData


class ExtractorPayload(BaseModel):
    virtual_lab: str
    data: NotebookData | None = None

    def __init__(self, **data):
        super().__init__(**data)


class NotebookExtractData(BaseModel):
    kernel: str
    notebook: Notebook
    user_name: str | None = None

    def __init__(self, **data):
        super().__init__(**data)


class NotebookExtractorPayload(BaseModel):
    virtual_lab: str
    data: NotebookExtractData | None = None

    def __init__(self, **data):
        super().__init__(**data)
