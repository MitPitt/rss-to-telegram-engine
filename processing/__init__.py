import importlib
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Dict, Type

from processing.base import ProcessingPipeline, Processor

logger = logging.getLogger(__name__)

_discovered_processors: Dict[str, Type[Processor]] = {}


def discover_processors(package_path: str = None, prefix: str = "") -> Dict[str, Type[Processor]]:
    global _discovered_processors

    if _discovered_processors and not prefix:
        return _discovered_processors

    if package_path is None:
        package_path = str(Path(__file__).parent)

    processors: Dict[str, Type[Processor]] = {}

    for importer, module_name, is_pkg in pkgutil.iter_modules([package_path]):
        if module_name in ("__init__", "base"):
            continue

        full_module_name = f"processing.{prefix.replace('/', '.')}{module_name}" if prefix else f"processing.{module_name}"

        if is_pkg:
            subdir_path = str(Path(package_path) / module_name)
            new_prefix = f"{prefix}{module_name}/"
            sub_processors = discover_processors(subdir_path, new_prefix)
            processors.update(sub_processors)
        else:
            try:
                module = importlib.import_module(full_module_name)

                for attr_name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, Processor) and obj is not Processor and obj.__module__ == module.__name__:
                        if not obj.name:
                            logger.warning(f"Processor {obj.__name__} has no 'name' attribute, skipping")
                            continue

                        full_name = f"{prefix}{obj.name}" if prefix else obj.name

                        processors[full_name] = obj
                        logger.debug(f"Discovered processor: {full_name} -> {obj.__name__}")

            except Exception as e:
                logger.error(f"Failed to import module {full_module_name}: {e}", exc_info=True)

    if not prefix:
        _discovered_processors = processors

    return processors


def create_pipeline() -> ProcessingPipeline:
    pipeline = ProcessingPipeline()
    processors = discover_processors()

    for name, processor_cls in processors.items():
        try:
            processor = processor_cls()
            pipeline.register(name, processor)
        except Exception as e:
            logger.error(f"Failed to instantiate processor {name}: {e}", exc_info=True)

    logger.info(f"Loaded {len(pipeline.processors)} processors: {', '.join(sorted(pipeline.processors.keys()))}")

    return pipeline


__all__ = ["create_pipeline"]
