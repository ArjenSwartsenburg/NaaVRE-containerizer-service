import abc
import json
import re
from typing import Union

from slugify import slugify

from app.models.notebook_data import NotebookData
from app.models.workflow_cell import Cell
from app.services.base_image.base_image_tags import BaseImageTags
from app.services.cell_extractor.cell_sanity_checker import \
    check_cell_title


class Extractor(abc.ABC):
    user_name: str
    cell_source: str
    cell_inputs: list
    cell_outputs: list
    cell_params: list
    cell_secrets: list
    cell_confs: list
    cell_dependencies: list
    kernel: str
    reserved_prefixes = ['param_', 'secret_', 'conf_']

    def __init__(self, notebook_data: NotebookData, base_image_tags_url: str):
        for cell in notebook_data.notebook.cells:
            if isinstance(cell.source, list):
                cell.source = "\n".join(cell.source)

        self.cell_source = (
            notebook_data.notebook.cells[notebook_data.cell_index].source)
        self.user_name = notebook_data.user_name
        self.kernel = notebook_data.kernel
        self.cell_inputs = self.get_cell_inputs()
        self.cell_outputs = self.get_cell_outputs()
        self.cell_params = self.get_cell_params()
        self.cell_secrets = self.get_cell_secrets()
        self.cell_confs = self.get_cell_confs()
        self.cell_dependencies = self.get_cell_dependencies(self.cell_confs)
        self.base_image_name = notebook_data.base_image_name
        self.base_image_tags_url = base_image_tags_url

    def get_cell(self) -> Cell:
        title = self.cell_source.partition('\n')[0].strip()
        if not title or title[0] != "#":
            title = "Untitled"
        check_cell_title(title)
        title = slugify(title)
        title += '-' + slugify(self.user_name)
        title = title.lower()
        base_image_tags = BaseImageTags(self.base_image_tags_url)
        cell_params = [
            {
                k: json.dumps(v) if not isinstance(v, Union[str | None]) else v
                for k, v in param.items()
            }
            for param in self.cell_params
        ]
        cell_dict = {
            'title': title,
            'params': cell_params,
            'secrets': self.cell_secrets,
            'inputs': self.cell_inputs,
            'outputs': self.cell_outputs,
            'confs': self.cell_confs,
            'dependencies': self.cell_dependencies,
            'base_container_image':
                base_image_tags.get_base_image_tag(self.base_image_name),
            'kernel': self.kernel,
            'original_source': self.clean_code()
        }
        cell = Cell.model_validate(cell_dict)
        return cell

    def clean_code(self):
        indices_to_remove = []
        lines = self.cell_source.split("\n")
        for line_i in range(0, len(lines)):
            line = lines[line_i]
            # Do not remove line that startswith param_ if not in the
            # self.params
            if line.startswith('param_'):
                # clean param name
                pattern = r"\b(param_\w+)\b"
                param_name = re.findall(pattern, line)[0]
                if param_name in self.cell_params:
                    indices_to_remove.append(line_i)
            regex = r'^\s*(#|import|from)'
            if re.match(regex, line):
                indices_to_remove.append(line_i)
        for ir in sorted(indices_to_remove, reverse=True):
            lines.pop(ir)
        original_source = "\n".join(lines)
        # Remove \n from the start and end of the string
        original_source = original_source.strip("\n")
        return original_source

    def not_reserved(self, var_name: str) -> bool:
        """
        Check if the variable name is not reserved
        :param var_name: variable name
        :return: True if the variable name is not reserved, False otherwise
        """
        for prefix in self.reserved_prefixes:
            if var_name.startswith(prefix):
                return False
        return True

    def is_complete(self) -> bool:
        """ Check if the cell header is complete, i.e. all required fields are
        defined in the header.
        """
        if (self.cell_inputs is None or
                self.cell_outputs is None or
                self.cell_params is None or
                self.cell_secrets is None or
                self.cell_confs is None or
                self.cell_dependencies is None):
            return False
        else:
            return True

    @abc.abstractmethod
    def get_cell_inputs(self) -> list[dict]:
        pass

    @abc.abstractmethod
    def get_cell_outputs(self) -> list[dict]:
        pass

    @abc.abstractmethod
    def get_cell_params(self) -> list[dict]:
        pass

    @abc.abstractmethod
    def get_cell_secrets(self) -> list[dict]:
        pass

    @abc.abstractmethod
    def get_cell_confs(self) -> list[dict]:
        pass

    @abc.abstractmethod
    def get_cell_dependencies(self, confs) -> list[dict]:
        pass


class DummyExtractor(Extractor):
    def get_cell_inputs(self) -> list[dict]:
        return []

    def get_cell_outputs(self) -> list[dict]:
        return []

    def get_cell_params(self) -> list[dict]:
        return []

    def get_cell_secrets(self) -> list[dict]:
        return []

    def get_cell_confs(self) -> list[dict]:
        return []

    def get_cell_dependencies(self, confs) -> list[dict]:
        return []

    def is_complete(self) -> bool:
        return True
