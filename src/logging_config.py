import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)
        
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data)


class StructuredLogger(logging.Logger):
    def _log_with_fields(self, level: int, msg: str, fields: Dict[str, Any] = None, **kwargs):
        extra = kwargs.get("extra", {})
        extra["extra_fields"] = fields or {}
        kwargs["extra"] = extra
        super()._log(level, msg, (), **kwargs)
    
    def info_with(self, msg: str, **fields):
        self._log_with_fields(logging.INFO, msg, fields)
    
    def error_with(self, msg: str, **fields):
        self._log_with_fields(logging.ERROR, msg, fields)
    
    def warning_with(self, msg: str, **fields):
        self._log_with_fields(logging.WARNING, msg, fields)
    
    def debug_with(self, msg: str, **fields):
        self._log_with_fields(logging.DEBUG, msg, fields)


logging.setLoggerClass(StructuredLogger)


def setup_logging(
    level: str = None,
    json_format: bool = None,
    log_file: str = None
):
    level = level or os.environ.get("AUTOWRKERS_LOG_LEVEL", "INFO")
    json_format = json_format if json_format is not None else os.environ.get("AUTOWRKERS_LOG_JSON", "0") == "1"
    log_file = log_file or os.environ.get("AUTOWRKERS_LOG_FILE")
    
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    root_logger.handlers.clear()
    
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(JSONFormatter() if json_format else formatter)
        root_logger.addHandler(file_handler)
    
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    
    return root_logger


def get_logger(name: str) -> StructuredLogger:
    return logging.getLogger(name)
