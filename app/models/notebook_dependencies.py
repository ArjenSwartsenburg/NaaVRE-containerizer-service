import logging
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class NotebookDependencies(BaseModel):
    dependencies: Optional[list[dict]] | None = None
