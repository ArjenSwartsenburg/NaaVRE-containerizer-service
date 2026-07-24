from typing import List, Optional

from pydantic_settings import BaseSettings

from app.models.vl_config import VLConfig


class ServiceSettings(BaseSettings):
    build_in_r_function_names: Optional[List[str]] = None
    vl_configurations: List[VLConfig]


class Settings:
    def __init__(self, config: dict = None):
        self.config = config
        self.service_settings = ServiceSettings(**config)

    def get_vl_config(self, virtual_lab) -> VLConfig:
        for setting in self.service_settings.vl_configurations:
            if setting.name == virtual_lab:
                return setting
        raise ValueError(f"Virtual lab '{virtual_lab}' not found in settings.")

    def get_built_in_function_names(self):
        return self.service_settings.build_in_r_function_names
