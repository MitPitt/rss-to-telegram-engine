import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Union

from core.models import Entry

logger = logging.getLogger(__name__)


class Processor(ABC):
    name: str = ""  # Subclasses must override this

    @abstractmethod
    async def process(self, entry: Entry, config: Dict[str, Any]) -> Entry:
        pass


class ProcessingPipeline:
    def __init__(self):
        self.processors: Dict[str, Processor] = {}

    def register(self, name: str, processor: Processor):
        self.processors[name] = processor
        logger.info(f"Registered processor: {name}")

    async def process(self, entry: Entry, processor_configs: Dict[str, Dict[str, Any]], global_config: Dict[str, Any] = None) -> Entry:
        if not processor_configs:
            return entry

        result = entry
        global_config = global_config or {}

        for name, proc_args in processor_configs.items():
            # Skip remaining processors if entry is filtered
            if result.filtered:
                remaining = len(processor_configs) - list(processor_configs.keys()).index(name)
                logger.info(f"Entry filtered, skipping remaining {remaining} processors")
                break

            # Ensure proc_args is a dict
            if proc_args is None:
                proc_args = {}
            elif not isinstance(proc_args, dict):
                logger.warning(f"Invalid config for processor '{name}': {proc_args}, using empty dict")
                proc_args = {}

            if name in self.processors:
                try:
                    logger.debug(f"Applying processor: {name} with args: {proc_args}")
                    # Merge global config with processor-specific args
                    merged_config = {**global_config, **proc_args}
                    result = await self.processors[name].process(result, merged_config)
                except Exception as e:
                    logger.error(f"Error in processor {name}: {e}", exc_info=True)
                    # Continue with other processors
            else:
                logger.warning(f"Processor not found: {name}")

        return result
