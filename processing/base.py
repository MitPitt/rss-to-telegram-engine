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

    async def process(self, entry: Entry, processor_configs: List[Dict[str, Any]], global_config: Dict[str, Any] = None) -> Entry:
        if not processor_configs:
            return entry

        result = entry
        global_config = global_config or {}

        for i, proc_config in enumerate(processor_configs):
            # Skip remaining processors if entry is filtered
            if result.filtered:
                remaining = len(processor_configs) - i
                logger.info(f"Entry filtered, skipping remaining {remaining} processors")
                break

            # Extract processor name from config
            if isinstance(proc_config, str):
                # Simple string form: "processor_name"
                name = proc_config
                proc_args = {}
            elif isinstance(proc_config, dict):
                name = proc_config.get("name")
                if not name:
                    logger.warning(f"Processor config missing 'name' field: {proc_config}, skipping")
                    continue
                # Copy config without 'name' key as args
                proc_args = {k: v for k, v in proc_config.items() if k != "name"}
            else:
                logger.warning(f"Invalid processor config type: {type(proc_config)}, skipping")
                continue

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
