import json
import os
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union

import requests
from pypushdeer import PushDeer
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from logging_config import init_logger


EXIT_FAILURE = 1
EXIT_AUTHENTICATION_FAILURE = 2
LOGIN_DOMAIN = "glados.cloud"
REQUEST_TIMEOUT = (15, 30)
POINTS_TIMEOUT = (10, 30)


class CheckinStatus(Enum):
    """签到状态"""

    SUCCESS = 0
    REPEAT = 1
    FAILURE = -2


class ExchangePlan(Enum):
    """兑换计划"""

    PLAN100 = "plan100"
    PLAN200 = "plan200"
    PLAN500 = "plan500"


class APIEndpoint(Enum):
    """API端点"""

    CHECKIN = "/api/user/checkin"
    STATUS = "/api/user/status"
    POINTS = "/api/user/points"
    EXCHANGE = "/api/user/exchange"


class LogEmoji:
    """日志 Emoji 常量"""

    SUCCESS = "✅"
    FAIL = "❌"
    REPEAT = "🔄"
    PENDING = "⏳"
    CHECKIN = "🎫"
    STATUS = "📊"
    POINTS = "💰"
    EXCHANGE = "🎁"
    START = "🚀"
    END = "🏁"
    COOKIE = "🍪"
    DOMAIN = "🌐"
    WARNING = "⚠️"
    ERROR = "🔴"
    INFO = "ℹ️"


def log_method(func):
    """日志装饰器"""

    def wrapper(self, *args, **kwargs):
        method_name = func.__name__
        emoji_map = {
            "checkin": LogEmoji.CHECKIN,
            "get_status": LogEmoji.STATUS,
            "get_points": LogEmoji.POINTS,
            "exchange": LogEmoji.EXCHANGE,
        }
        emoji = emoji_map.get(method_name, LogEmoji.INFO)
        try:
            result = func(self, *args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.ERROR} {method_name} 执行失败: {e}")

            DEFAULT_ERRORS = {
                "checkin": {"status": "签到失败", "points": "0", "message": ""},
                "get_status": ("None 天", None),
                "get_points": ("None 积分", 0),
                "exchange": "",
            }

            if method_name in DEFAULT_ERRORS:
                error_template = DEFAULT_ERRORS[method_name]
                if isinstance(error_template, dict):
                    error_result = error_template.copy()
                    error_result["message"] = f"执行失败: {e}"
                    return error_result
                return error_template
            raise

    return wrapper


@dataclass(frozen=True)
class Account:
    cookie: str = field(repr=False)
    name: str = ""
    user_agent: str = ""

    @classmethod
    def from_json(cls, item: object, position: int) -> "Account":
        label = f"GLADOS_COOKIES 第 {position} 项"
        allowed_fields = {"cookie", "name", "origin", "user-agent"}
        if not isinstance(item, dict) or item.keys() - allowed_fields:
            raise ValueError(f"{label}必须是账号对象，只允许 cookie、name、origin、user-agent")
        if any(not isinstance(value, str) for value in item.values()):
            raise ValueError(f"{label}的字段必须是字符串")

        origin = item.get("origin", "").strip().lower()
        if origin not in ("", LOGIN_DOMAIN):
            raise ValueError(f"{label}的 origin 必须为 {LOGIN_DOMAIN}")
        cookie = item.get("cookie", "")
        for part in cookie.split(";"):
            key, _, value = part.partition("=")
            if key.strip() in ("gld:sess", "koa:sess") and value.strip(' \t"'):
                break
        else:
            raise ValueError(f"{label}的 cookie 缺少有效的 gld:sess 或 koa:sess 字段")
        return cls(
            cookie=cookie.strip(" \t"),
            name=item.get("name", "").strip(),
            user_agent=item.get("user-agent", "").strip(" \t"),
        )


class Config:
    """应用配置"""

    ENV_PUSH_KEY = "PUSHDEER_SENDKEY"
    ENV_SERVERCHAN_KEY = "SERVERCHAN_SENDKEY"
    ENV_COOKIES = "GLADOS_COOKIES"
    ENV_EXCHANGE_PLAN = "GLADOS_EXCHANGE_PLAN"
    ENV_VERBOSE = "GLADOS_VERBOSE"

    """默认兑换计划（none 表示不自动兑换）"""
    DEFAULT_EXCHANGE_PLAN = "none"

    """默认是否输出详细响应"""
    DEFAULT_VERBOSE = False

    """兑换计划列表"""
    EXCHANGE_PLANS = {
        ExchangePlan.PLAN100.value: 100,
        ExchangePlan.PLAN200.value: 200,
        ExchangePlan.PLAN500.value: 500,
    }

    def __init__(self):
        self.push_key: str = ""
        self.serverchan_key: str = ""
        self.accounts: List[Account] = []
        self.exchange_plan: str = self.DEFAULT_EXCHANGE_PLAN
        self.verbose: bool = self.DEFAULT_VERBOSE
        self._load_config()

    def _load_config(self) -> None:
        """加载配置"""
        push_key_env: Optional[str] = os.environ.get(self.ENV_PUSH_KEY)
        serverchan_key_env: Optional[str] = os.environ.get(self.ENV_SERVERCHAN_KEY)
        raw_cookies_env = os.environ.get(self.ENV_COOKIES, "").lstrip("\ufeff").strip()
        exchange_plan_env: Optional[str] = os.environ.get(self.ENV_EXCHANGE_PLAN)
        verbose_env: Optional[str] = os.environ.get(self.ENV_VERBOSE)

        if not push_key_env:
            logger.warning(f"{LogEmoji.WARNING} 环境变量 '{self.ENV_PUSH_KEY}' 未设置。")
            self.push_key = ""
        else:
            self.push_key = push_key_env

        if not serverchan_key_env:
            logger.warning(f"{LogEmoji.WARNING} 环境变量 '{self.ENV_SERVERCHAN_KEY}' 未设置。")
            self.serverchan_key = ""
        else:
            self.serverchan_key = serverchan_key_env

        if not raw_cookies_env:
            logger.warning(f"{LogEmoji.WARNING} 环境变量 '{self.ENV_COOKIES}' 未设置。")
        else:
            self.accounts = self._load_accounts(raw_cookies_env)

        if not exchange_plan_env:
            logger.warning(f"{LogEmoji.WARNING} 环境变量 '{self.ENV_EXCHANGE_PLAN}' 未设置，将使用默认设置（不自动兑换）。")
            self.exchange_plan = self.DEFAULT_EXCHANGE_PLAN
        else:
            if exchange_plan_env in self.EXCHANGE_PLANS:
                self.exchange_plan = exchange_plan_env
                logger.info(f"{LogEmoji.SUCCESS} 使用指定的兑换计划: {self.exchange_plan}")
            else:
                logger.warning(f"{LogEmoji.WARNING} 环境变量 '{self.ENV_EXCHANGE_PLAN}' 的值无效，将跳过自动兑换。")
                self.exchange_plan = self.DEFAULT_EXCHANGE_PLAN

        logger.info(f"{LogEmoji.INFO} 共加载了 {len(self.accounts)} 个账号用于签到。")
        logger.info(f"{LogEmoji.INFO} 当前 {self.ENV_PUSH_KEY} {'已设置' if push_key_env else '未设置'}，{self.ENV_SERVERCHAN_KEY} {'已设置' if serverchan_key_env else '未设置'}。")
        logger.info(f"{LogEmoji.INFO} 当前 {self.ENV_EXCHANGE_PLAN}: {self.exchange_plan}。")

        if verbose_env is not None:
            verbose_env_lower = verbose_env.lower()
            if verbose_env_lower in ["true", "1", "yes", "y"]:
                self.verbose = True
            elif verbose_env_lower in ["false", "0", "no", "n"]:
                self.verbose = False
            else:
                logger.warning(f"{LogEmoji.WARNING} 环境变量 '{self.ENV_VERBOSE}' 的值无效，将使用默认值 {self.DEFAULT_VERBOSE}。")

        logger.info(f"{LogEmoji.INFO} 当前 {self.ENV_VERBOSE}: {self.verbose}。")

    @staticmethod
    def _load_accounts(raw: str) -> List[Account]:
        if raw.startswith(("[", "{")):
            try:
                items = json.loads(raw)
            except json.JSONDecodeError:
                raise ValueError("GLADOS_COOKIES 不是有效的 JSON 数组") from None
            if not isinstance(items, list) or not items:
                raise ValueError("GLADOS_COOKIES 必须是非空的账号 JSON 数组")
            accounts = [Account.from_json(item, i) for i, item in enumerate(items, 1)]
        else:
            accounts = [Account(part.strip()) for part in raw.split("&") if part.strip()]
            if not accounts:
                raise ValueError("GLADOS_COOKIES 未包含有效的 Cookie")

        unique: Dict[str, Account] = {}
        for i, account in enumerate(accounts, 1):
            header_values = (account.cookie, account.user_agent)
            if any(value and not (value.isascii() and value.isprintable()) for value in header_values):
                raise ValueError(f"GLADOS_COOKIES 第 {i} 项的 Cookie 或 User-Agent 含非法请求头字符")
            if account.name and not account.name.isprintable():
                raise ValueError(f"GLADOS_COOKIES 第 {i} 项的账号名含控制字符")
            unique.setdefault(account.cookie, account)
        if len(unique) < len(accounts):
            logger.warning(f"{LogEmoji.WARNING} 已跳过 {len(accounts) - len(unique)} 个重复 Cookie")
        return list(unique.values())


# 各平台合法 User-Agent（版本号无关，仅平台 token 关键）
PLATFORM_UA = {
    "Windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "macOS": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Linux": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "iPhone": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Android": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
}


def parse_number(value: object) -> Optional[int]:
    if type(value) is int:
        return value
    if isinstance(value, (str, float)):
        try:
            return int(float(value))
        except (ValueError, OverflowError):
            pass
    return None


class API:
    """API 调用"""

    CHECKIN_URL = APIEndpoint.CHECKIN.value
    STATUS_URL = APIEndpoint.STATUS.value
    POINTS_URL = APIEndpoint.POINTS.value
    EXCHANGE_URL = APIEndpoint.EXCHANGE.value

    def __init__(self, domain: str, cookie_index: int = 0, verbose: bool = False, user_agent: str = ""):
        self.domain: str = domain
        self.cookie_index: int = cookie_index
        self.verbose: bool = verbose
        self.user_agent = user_agent
        self.authentication_failed = False
        self.headers: Dict[str, str] = self._get_headers()
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        # Retry only the status lookup. The other endpoints use the default
        # zero-retry adapter, including on connection errors.
        retry_strategy = Retry(
            total=2,
            read=0,
            other=0,
            backoff_factor=0.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=False,
            raise_on_status=False,
        )
        self.session.mount(self._get_full_url(self.STATUS_URL), HTTPAdapter(max_retries=retry_strategy))

    def __del__(self):
        """关闭 session"""
        self.close()

    def close(self) -> None:
        """关闭 session"""
        if hasattr(self, "session"):
            try:
                self.session.close()
            except Exception as e:
                logger.error(f"{LogEmoji.ERROR} 关闭 session 时发生错误: {e}")

    def __enter__(self):
        """进入上下文管理器"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文管理器"""
        self.close()
        return False

    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        return {
            "origin": f"https://{self.domain}",
            "user-agent": self.user_agent or PLATFORM_UA["Windows"],
        }

    def _log(self, level: str, emoji: str, message: str, force: bool = False) -> None:
        """统一日志输出方法"""

        prefix = f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}]"
        log_message = f"{prefix} {emoji} {message}"

        if force or self.verbose:
            if level == "info":
                logger.info(log_message)
            elif level == "warning":
                logger.warning(log_message)
            elif level == "error":
                logger.error(log_message)

    def _get_full_url(self, path: str) -> str:
        """获取完整 URL"""
        return f"https://{self.domain}{path}"

    def _make_request(self, url: str, method: str, data: Optional[Dict] = None, cookies: str = "", user_agent: Optional[str] = None) -> Optional[requests.Response]:
        """发送 HTTP 请求"""
        session_headers = self.headers.copy()
        if user_agent:
            session_headers["user-agent"] = user_agent
        session_headers["cookie"] = cookies

        try:
            if method.upper() == "POST":
                response = self.session.post(
                    url, headers=session_headers, data=data,
                    timeout=REQUEST_TIMEOUT, allow_redirects=False,
                )
            elif method.upper() == "GET":
                timeout = POINTS_TIMEOUT if url == self._get_full_url(self.POINTS_URL) else REQUEST_TIMEOUT
                response = self.session.get(url, headers=session_headers, timeout=timeout, allow_redirects=False)
            else:
                self._log("error", LogEmoji.ERROR, f"不支持的 HTTP 方法: {method}", force=True)
                return None

            if not 200 <= response.status_code < 300:
                if response.status_code in (401, 403):
                    self.authentication_failed = True
                self._log("warning", LogEmoji.WARNING, f"向 {url} 发起的请求失败，HTTP {response.status_code}", force=True)
                return None
            return response
        except requests.exceptions.RequestException as e:
            self._log("error", LogEmoji.ERROR, f"向 {url} 发起请求时发生网络错误: {type(e).__name__}", force=True)
            return None

    def _get_checkin_data(self) -> Dict[str, str]:
        """获取签到数据"""
        return {"token": self.domain}

    @log_method
    def checkin(self, cookies: str) -> Dict[str, Union[str, CheckinStatus]]:
        """执行签到，含设备平台自适应重试"""
        url = self._get_full_url(self.CHECKIN_URL)
        checkin_data = self._get_checkin_data()

        used_ua = self.headers["user-agent"]
        raw = self._checkin_attempt(url, checkin_data, cookies, used_ua)

        # 显式配置的 UA 用于整个会话。缺省时只针对明确的设备不匹配重试一次。
        if (
            not self.user_agent and raw is not None
            and type(raw.get("code")) is int and raw["code"] == 4
            and raw.get("reason") == "device-mismatch"
        ):
            login_device = raw.get("loginDevice")
            if login_device == "iOS":
                login_device = "iPhone"
            recover_ua = PLATFORM_UA.get(login_device) if isinstance(login_device, str) else None
            if recover_ua and recover_ua != used_ua:
                self._log("warning", LogEmoji.WARNING, f"设备平台不匹配 (loginDevice={login_device})，切换 UA 重试", force=True)
                used_ua = recover_ua
                raw = self._checkin_attempt(url, checkin_data, cookies, used_ua)

        result = self._parse_checkin(raw)
        if result["code"] in (CheckinStatus.SUCCESS, CheckinStatus.REPEAT):
            self.headers["user-agent"] = used_ua
        return result

    def _checkin_attempt(self, url: str, data: Dict, cookies: str, user_agent: str) -> Optional[Dict]:
        """发起一次签到请求并返回解析后的响应体（失败返回 None）"""
        response = self._make_request(url, "POST", data, cookies, user_agent=user_agent)
        if response is None:
            return None
        try:
            raw = response.json()
        except ValueError:
            self._log("error", LogEmoji.ERROR, "签到响应解析失败", force=True)
            return None

        if not isinstance(raw, dict):
            self._log("error", LogEmoji.ERROR, "签到响应格式异常", force=True)
            return None
        return raw

    def _parse_checkin(self, raw: Optional[Dict]) -> Dict[str, Union[str, CheckinStatus]]:
        """解析签到响应为结果字典"""
        result: Dict[str, Union[str, CheckinStatus]] = {
            "status": "签到失败",
            "points": "0",
            "message": "",
            "code": CheckinStatus.FAILURE,
        }

        if raw is None:
            result["message"] = "网络请求失败"
            return result

        code = raw.get("code")
        if type(code) is not int:
            self._log("error", LogEmoji.ERROR, "签到响应缺少有效的状态码", force=True)
            return result
        if code == -2:
            self.authentication_failed = True
        message = str(raw.get("message", "无消息字段"))
        if code != -2 and "please checkin via" in message.lower():
            result["message"] = "服务端拒绝当前签到域名，请确认 Cookie 来自登录域名"
            self._log("error", LogEmoji.ERROR, result["message"], force=True)
            return result

        if code == CheckinStatus.SUCCESS.value:
            points = parse_number(raw.get("points"))
            records = raw.get("list")
            if isinstance(records, list) and records and isinstance(records[0], dict):
                change = parse_number(records[0].get("change"))
                if change is not None:
                    points = change
            points = str(points or 0)
            self._log("info", LogEmoji.SUCCESS, f"{{ code : {code}, points : {points}, message : {message} }}")
            result["code"] = CheckinStatus.SUCCESS
            result["status"] = "签到成功"
            result["points"] = points
            result["message"] = message
        elif code == CheckinStatus.REPEAT.value:
            self._log("info", LogEmoji.REPEAT, f"{{ code : {code}, message : {message} }}", force=True)
            result["code"] = CheckinStatus.REPEAT
            result["status"] = "重复签到"
            result["points"] = "0"
            result["message"] = message
        else:
            # 失败分支：按 code 细分诊断，便于快速定位根因
            if code == 4:
                reason = raw.get("reason")
                if reason == "device-mismatch":
                    self._log("error", LogEmoji.ERROR, "设备平台不匹配，请核对账号配置的 user-agent 与提取 Cookie 时的浏览器是否一致", force=True)
                else:
                    self._log("error", LogEmoji.ERROR, f"服务端拒绝签到(code=4, reason={reason})，请检查账户风控状态", force=True)
            elif code == -2:
                self._log("error", LogEmoji.ERROR, f"无权限(code=-2)：Cookie 可能已失效，请重新登录 {self.domain} 刷新 Cookie 并更新仓库 Secret GLADOS_COOKIES 后重试", force=True)
            else:
                self._log("error", LogEmoji.ERROR, f"未预期响应(code={code}, message={message})，请检查 GLaDOS 服务状态或 Cookie", force=True)
            result["code"] = CheckinStatus.FAILURE
            result["status"] = "签到失败"
            result["points"] = "0"
            result["message"] = message

        return result

    @log_method
    def get_status(self, cookies: str) -> Tuple[str, Optional[int]]:
        """获取状态"""

        url = self._get_full_url(self.STATUS_URL)
        response = self._make_request(url, "GET", cookies=cookies)

        if response is None:
            return "None 天", None
        data = response.json()
        code = data.get("code") if isinstance(data, dict) else None
        if type(code) is not int:
            return "None 天", None
        if code == -2:
            self.authentication_failed = True
        if code != 0:
            return "None 天", code

        status = data.get("data")
        if not isinstance(status, dict):
            return "None 天", None
        days = parse_number(status.get("leftDays"))
        self._log("info", LogEmoji.SUCCESS, f"{{ code : {code}, leftDays : {days} 天}}")
        return f"{days} 天", code

    @log_method
    def get_points(self, cookies: str) -> Tuple[str, int]:
        """获取积分"""
        url = self._get_full_url(self.POINTS_URL)
        response = self._make_request(url, "GET", cookies=cookies)

        if response:
            data = response.json()
            code = data.get("code")
            if code == -2:
                self.authentication_failed = True
                return "None 积分", 0
            points = parse_number(data.get("points"))

            if points is not None:
                self._log("info", LogEmoji.SUCCESS, f"{{ code : {code}, points : {points} 积分}}")
                return f"{points} 积分", points
            else:
                self._log("info", LogEmoji.FAIL, f"{{ code : {code}, points : {points} 积分}}")
                return "None 积分", 0
        else:
            self._log("warning", LogEmoji.WARNING, "获取积分失败")
            return "None 积分", 0

    @log_method
    def exchange(self, cookies: str, plan: str, required_points: int) -> str:
        """执行兑换"""
        url = self._get_full_url(self.EXCHANGE_URL)
        response = self._make_request(url, "POST", {"planType": plan}, cookies)

        if response:
            data = response.json()
            code = data.get("code", -2)
            message = data.get("message", "未知错误")

            if code == 0:
                self._log("info", LogEmoji.SUCCESS, f"{{ code : {code}, message : {message} }}")
                return f"兑换成功: {plan}"
            else:
                self._log("info", LogEmoji.FAIL, f"{{ code : {code}, message : {message} }}", force=True)
                return f"兑换失败: {message}"
        else:
            self._log("warning", LogEmoji.WARNING, "兑换失败", force=True)
            return "兑换失败"


@dataclass()
class CheckinResult:
    """签到结果"""

    cookie_index: int
    domain: str
    status: str = "签到失败"
    points: str = "0"
    days: str = "None"
    points_total: str = "None"
    exchange: str = "未兑换"
    code: CheckinStatus = CheckinStatus.FAILURE  # 0: 成功, 1: 重复, -2: 失败
    authentication_failed: bool = False

    def to_dict(self) -> Dict[str, Union[str, CheckinStatus]]:
        result_dict = asdict(self)
        return result_dict


def render_payload(payload: dict, line_sep: str = "\n") -> str:
    """将结构化签到结果渲染为文本。line_sep 控制行分隔：
    PushDeer 用 \\n（原生换行），Server酱 用 '  \\n'（Markdown 硬换行，避免段间空行）。"""
    lines = []
    for r in payload.get("rows", []):
        line = (
            f"#{r['index']:<2}  {r['emoji']} {r['status']:<6}  {r['domain']:<13}  "
            f"获得 {r['points']} 积分 · 剩余 {r['days']} · 总 {r['points_total']}"
        )
        if r.get("exchange"):
            line += f" · {r['exchange']}"
        lines.append(line)
    note = payload.get("note")
    if note:
        lines.append(note)
    return line_sep.join(lines)


class PushBackend:
    """推送后端抽象基类（策略模式）"""

    name: str = "base"

    def send(self, title: str, payload: dict) -> bool:
        """发送推送，返回是否成功（由子类实现）"""
        raise NotImplementedError

    def _render(self, payload: dict) -> str:
        """将结构化结果渲染为本后端适用的文本（子类可重写行分隔方式）"""
        return render_payload(payload, "\n")


class PushDeerBackend(PushBackend):
    """PushDeer 后端（官方 pypushdeer 库）"""

    name = "PushDeer"

    def __init__(self, push_key: str):
        self.push_key = push_key

    def send(self, title: str, payload: dict) -> bool:
        try:
            PushDeer(pushkey=self.push_key).send_text(title, desp=self._render(payload))
            return True
        except Exception as e:
            logger.error(f"{LogEmoji.ERROR} [{self.name}] 推送失败: {e}")
            return False


class ServerChanBackend(PushBackend):
    """Server酱 后端（官方 serverchan_sdk）"""

    name = "Server酱"

    def __init__(self, send_key: str):
        self.send_key = send_key

    def _render(self, payload: dict) -> str:
        # Server酱 按 Markdown 渲染，单 \n 不换行；用行尾两空格+\n 做硬换行，避免段间空行
        return render_payload(payload, "  \n")

    def send(self, title: str, payload: dict) -> bool:
        # 延迟导入：未安装 SDK 时不阻断其他后端（如 PushDeer）
        try:
            from serverchan_sdk import sc_send
        except ImportError:
            logger.error(f"{LogEmoji.ERROR} [{self.name}] 推送失败: 未安装 serverchan_sdk，请先执行 pip install serverchan-sdk。")
            return False
        try:
            resp = sc_send(sendkey=self.send_key, title=title, desp=self._render(payload))
            if isinstance(resp, dict) and resp.get("code") == 0:
                return True
            logger.error(f"{LogEmoji.ERROR} [{self.name}] 推送失败: 接口返回 {resp}")
            return False
        except Exception as e:
            logger.error(f"{LogEmoji.ERROR} [{self.name}] 推送失败: {e}")
            return False


class PushService:
    """推送服务：聚合多个后端，向全部已配置后端发送（都发）"""

    def __init__(self, config: Config):
        self.backends: List[PushBackend] = []
        if config:
            if getattr(config, "push_key", ""):
                self.backends.append(PushDeerBackend(config.push_key))
            if getattr(config, "serverchan_key", ""):
                self.backends.append(ServerChanBackend(config.serverchan_key))

    def send(self, title: str, payload: dict) -> bool:
        """向所有已配置后端发送推送，只要有一个成功即视为成功"""
        if not self.backends:
            logger.info(f"{LogEmoji.WARNING} 未配置推送密钥，跳过。")
            return False

        backend_names = "、".join(b.name for b in self.backends)
        logger.info(f"{LogEmoji.INFO} 已配置 {len(self.backends)} 个后端（{backend_names}），开始发送")
        results = [backend.send(title, payload) for backend in self.backends]
        if any(results):
            logger.info(f"{LogEmoji.SUCCESS} 发送完成：{sum(results)}/{len(results)} 个后端成功")
            return True
        logger.error(f"{LogEmoji.ERROR} 全部后端发送失败")
        return False


class Checker:
    """签到"""

    def __init__(self, config: Config):
        self.config = config
        self.results = []

    def _log(self, cookie_idx: int, domain: str, emoji: str, message: str, force: bool = False) -> None:
        """统一日志输出方法"""

        if self.config.verbose or force:
            logger.info(f"{LogEmoji.COOKIE}[{cookie_idx}] {LogEmoji.DOMAIN}[{domain}] {emoji} {message}")

    def checkin_all(self):
        """逐个处理账号。部署只访问 Cookie 的登录域名。"""
        for cookie_idx, account in enumerate(self.config.accounts, 1):
            label = f"{cookie_idx} ({account.name})" if account.name else str(cookie_idx)
            logger.info(f"{LogEmoji.START} ========== 开始处理账号 {label} ==========")
            result = self._checkin_account(account, cookie_idx)
            self.results.append(result)

            result_message = f"结果: {result.status}"
            if result.code == CheckinStatus.SUCCESS:
                if self.config.verbose:
                    result_message = f"结果: {result.status}, 获得 {result.points} 积分, 剩余 {result.days}, 总 {result.points_total}, {result.exchange}"
                self._log(cookie_idx, LOGIN_DOMAIN, LogEmoji.SUCCESS, result_message, force=True)
            else:
                self._log(cookie_idx, LOGIN_DOMAIN, LogEmoji.WARNING, result_message, force=True)

    def _checkin_account(self, account: Account, cookie_idx: int) -> CheckinResult:
        domain = LOGIN_DOMAIN
        cookie = account.cookie
        result = CheckinResult(cookie_idx, domain)

        with API(domain, cookie_idx, verbose=self.config.verbose, user_agent=account.user_agent) as api:
            self._log(cookie_idx, domain, LogEmoji.STATUS, "查询剩余天数")
            days_str, status_code = api.get_status(cookie)
            result.days = days_str
            if api.authentication_failed:
                result.authentication_failed = True
                result.status = "凭据失效或访问被拒绝"
                return result
            if status_code != 0:
                result.status = "状态查询未确认登录，跳过签到"
                return result

            self._log(cookie_idx, domain, LogEmoji.CHECKIN, "执行签到")
            checkin_result = api.checkin(cookie)
            result.status = checkin_result["status"]
            result.code = checkin_result.get("code", CheckinStatus.FAILURE)
            result.points = str(checkin_result.get("points", "0"))
            if result.code == CheckinStatus.FAILURE:
                result.authentication_failed = api.authentication_failed
                return result

            self._log(cookie_idx, domain, LogEmoji.POINTS, "查询总积分")
            points_str, _ = api.get_points(cookie)
            result.points_total = points_str

            required_points = self.config.EXCHANGE_PLANS.get(self.config.exchange_plan)
            if required_points is None:
                result.exchange = "未配置兑换计划，跳过自动兑换"
                self._log(cookie_idx, domain, LogEmoji.INFO, "未配置兑换计划，跳过自动兑换")
            elif not api.authentication_failed:
                self._log(
                    cookie_idx,
                    domain,
                    LogEmoji.EXCHANGE,
                    f"开始兑换 {self.config.exchange_plan} (需要 {required_points} 积分)",
                )
                result.exchange = api.exchange(cookie, self.config.exchange_plan, required_points)

        return result

    def get_results(self) -> List[Dict[str, str]]:
        """获取所有结果"""
        return [result.to_dict() for result in self.results]

    def format_results(self) -> dict:
        """格式化结果为结构化 payload（表现交给各推送后端/日志各自渲染）"""
        results = self.get_results()

        success_count = sum(1 for r in results if r["code"] == CheckinStatus.SUCCESS)
        repeat_count = sum(1 for r in results if r["code"] == CheckinStatus.REPEAT)
        fail_count = sum(1 for r in results if r["code"] == CheckinStatus.FAILURE)

        status_emoji_map = {
            CheckinStatus.SUCCESS: LogEmoji.SUCCESS,
            CheckinStatus.REPEAT: LogEmoji.REPEAT,
            CheckinStatus.FAILURE: LogEmoji.ERROR,
        }
        title = f"GLaDOS 签到 · 成功 {success_count} · 失败 {fail_count} · 重复 {repeat_count}"

        rows = []
        for i, res in enumerate(results, 1):
            rows.append({
                "index": i,
                "emoji": status_emoji_map.get(res["code"], LogEmoji.ERROR),
                "status": res["status"],
                "domain": res["domain"],
                "points": res["points"],
                "days": res["days"],
                "points_total": res["points_total"],
                "exchange": res["exchange"],
            })

        return {
            "title": title,
            "rows": rows,
            "success": success_count,
            "fail": fail_count,
            "repeat": repeat_count,
        }


logger = init_logger()


def main():
    """主函数"""
    exit_code = 0
    try:
        logger.info(f"{LogEmoji.START} 步骤 1: 加载配置")
        config = Config()

        if not config.accounts:
            logger.error(f"{LogEmoji.ERROR} 未找到有效的 Cookie, 退出程序。")
            payload = {"title": "# 未找到 cookies!", "rows": []}
            exit_code = EXIT_AUTHENTICATION_FAILURE
        else:
            logger.info(f"{LogEmoji.START} 步骤 2: 执行签到")
            checker = Checker(config)
            checker.checkin_all()

            logger.info(f"{LogEmoji.START} 步骤 3: 格式化结果")
            payload = checker.format_results()
            if payload["fail"] > 0:
                exit_code = (
                    EXIT_AUTHENTICATION_FAILURE
                    if any(result.authentication_failed for result in checker.results)
                    else EXIT_FAILURE
                )
            logger.info(f"\n{LogEmoji.END} ========== 签到总结 ==========\n{payload['title']}\n{render_payload(payload)}")

    except Exception as e:
        logger.error(f"{LogEmoji.ERROR} 主程序执行过程中发生未预期的错误: {e}")
        payload = {"title": "# 脚本执行出错", "rows": [], "note": str(e)}
        exit_code = EXIT_FAILURE

    logger.info(f"{LogEmoji.START} 步骤 4: 发送推送")
    push_service = PushService(config if "config" in locals() else None)
    push_service.send(payload["title"], payload)
    logger.info(f"{LogEmoji.END} 签到完成")

    if exit_code != 0:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
