import functools
import json
import logging
import os
import time
import traceback
from datetime import UTC, datetime
from logging import FileHandler


def validate_msg(msg):
    if isinstance(msg, dict):
        try:
            json.dumps(msg)
            return msg
        except Exception as e:
            return {k: str(v) for k, v in msg.items()}
    return str(msg)


class JSONLineFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "level": record.levelname,
            "timestamp": datetime.now(UTC).isoformat(),
            **validate_msg(record.msg),
        }

        if record.exc_info:
            log_record["exception"] = "".join(
                # log exception stacktrace
                traceback.format_exception(*record.exc_info)
            )

        return json.dumps(log_record)


def get_logger(file_path=None) -> logging.Logger:
    if file_path is None:
        file_path = os.getenv("LOGGER_PATH", "log.jsonl")

    logger = logging.getLogger("request_json_logger")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = FileHandler(file_path)
        handler.setFormatter(JSONLineFormatter())
        logger.addHandler(handler)

    logger.propagate = False
    return logger


default_logger = None


def get_default_logger():
    global default_logger
    if default_logger is None:
        default_logger = get_logger()
    return default_logger


def log_event(sender: str, event_type: str, payload: dict, success: bool = True):
    default_logger = get_default_logger()
    default_logger.info(
        {
            "from": sender,
            "event_type": event_type,
            "success": success,
            "payload": payload,
            "": 0,
        }
    )


class TimedEventContextManager:
    def __init__(self, sender: str):
        self.sender = sender
        self.start_time = None
        self.end_time = None
        self.payload = None
        self.success = True
        self.response_time = 0

    def __enter__(self):
        default_logger = get_default_logger()
        default_logger.info(
            {
                "from": self.sender,
                "event_type": "start_event",
                "success": True,
                "payload": {},
                "response_time": 0,
            }
        )
        self.start_time = time.time()
        return self

    def register_payload(self, payload: dict, success: bool = True):
        self.payload = payload
        self.success = success

    def __exit__(self, exc_type, exc_value, traceback):
        self.end_time = time.time()
        self.response_time = self.end_time - self.start_time
        default_logger = get_default_logger()
        log_payload = {
            "from": self.sender,
            "event_type": "end_event",
            "success": self.success,
            "payload": self.payload,
            "end_time": self.end_time,
            "start_time": self.start_time,
            "response_time": self.response_time,
        }
        if self.success:
            default_logger.info(log_payload)
        else:
            default_logger.error(log_payload)


def timed_event_context(sender: str):
    return TimedEventContextManager(sender)


def log_event_decorator(log_payload: bool = True):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            sender = str(func.__qualname__)
            with TimedEventContextManager(sender) as context:
                try:
                    result = func(*args, **kwargs)
                    if log_payload:
                        context.register_payload(result, success=True)
                    return result
                except Exception as e:
                    context.register_payload(e, success=False)
                    raise e

        return wrapper

    return decorator


def _sanity_check():
    logger = get_logger("test.jsonl")
    logger.info("test")
    logger.info({"key": "val"})
    try:
        1 / 0
    except Exception as e:
        # Exception stacktrace gets logged as well
        logger.exception("Unhandled exception occurred")
        logger.exception(*{"meta": "some meta"})


if __name__ == "__main__":
    _sanity_check()
