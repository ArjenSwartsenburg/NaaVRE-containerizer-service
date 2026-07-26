import logging

from pydantic import BaseModel

from app.models.notebook import Notebook

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class NotebookData(BaseModel):
    cell_index: int
    kernel: str
    notebook: Notebook
    user_name: str | None = None
    base_image_name: str | None = None

    def __init__(self, **data):
        super().__init__(**data)
        if not self.base_image_name:
            self.set_base_image_name()

    def set_user_name(self, user_name: str):
        self.user_name = user_name
        return self

    # We could try a smart way of detecting the dependencies and decide the
    # flavor base image
    def set_base_image_name(self):
        # Tolerant matching: standard kernelspec names are e.g. 'python3',
        # 'ir', 'julia-1.10' — exact comparisons left base_image_name None
        # and made extraction fail with 'Base image name None not found'.
        kl = self.kernel.lower()
        if 'python' in kl or kl == 'ipython':
            self.base_image_name = "python"
        elif kl in ('r', 'irkernel') or kl.startswith('ir'):
            self.base_image_name = "r"
        elif 'julia' in kl:
            self.base_image_name = "julia"
        elif kl == 'c':
            self.base_image_name = "c"
