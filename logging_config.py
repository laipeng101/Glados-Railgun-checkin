from copy import copy
import datetime
import logging
import logging.config
import re


def beijing_time_converter(timestamp):
    """将时间转换为北京时间"""
    utc_dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    beijing_tz = datetime.timezone(datetime.timedelta(hours=8))
    beijing_dt = utc_dt.astimezone(beijing_tz)
    return beijing_dt.timetuple()


LEVEL_TAGS = {
    "DEBUG": "[D]",
    "INFO": "[I]",
    "WARNING": "[W]",
    "ERROR": "[E]",
    "CRITICAL": "[C]",
}


SECRET_PATTERNS = (
    (re.compile(r'("cookie"\s*:\s*)"(?:\\.|[^"\\])*"', re.IGNORECASE), r'\1"[REDACTED]"'),
    (re.compile(r"""\b((?:koa|gld):[\w.-]+[ \t]*=[ \t]*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^;\s"',}]+)"""),
     r"\1[REDACTED]"),
    (re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE), r"\1[REDACTED]"),
)


class AlignedFormatter(logging.Formatter):
    """用北京时间和短级别标签格式化日志，并脱敏会话字段。"""

    converter = staticmethod(beijing_time_converter)

    def format(self, record):
        record = copy(record)
        record.levelname = LEVEL_TAGS.get(record.levelname, f"[{record.levelname[0]}]")
        message = super().format(record)
        for pattern, replacement in SECRET_PATTERNS:
            message = pattern.sub(replacement, message)
        return message


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "()": "logging_config.AlignedFormatter",
            "format": "%(asctime)s - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": "INFO",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}


def init_logger():
    """初始化日志"""
    logging.config.dictConfig(LOGGING_CONFIG)
    return logging.getLogger()
