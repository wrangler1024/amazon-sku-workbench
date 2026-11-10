"""163 IMAP 协议层。纯 stdlib + 全局串行锁。

关键点(继承自 store_mail_assistant v1 的实测结论):
1. 163 要求 IMAP 客户端在 LOGIN 后、SELECT 前发送非标准 ID 命令声明
   客户端身份,否则 SELECT 返回 "Unsafe Login"。imaplib 默认不发,必须手动补。
2. 163 对并发/频繁连接敏感,所有 IMAP 操作全局串行排队。
3. 拉信必须用 BODY.PEEK[](而不是 RFC822),否则一拉就置 \\Seen,
   未读状态会被同步动作污染。
"""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import logging
import re
import socket
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import FetchedMail

logger = logging.getLogger(__name__)

BODY_HTML_MAX = 512 * 1024      # 原始 HTML 入库上限
BODY_TEXT_MAX = 256 * 1024      # 纯文本入库上限


# ---------- 异常分类 ----------

class ImapError(Exception):
    """IMAP 操作失败基类。"""


class ImapAuthError(ImapError):
    """授权码失效或错误(不自动重试,需运营重新生成授权码)。"""


class ImapNetworkError(ImapError):
    """网络抖动/超时(可退避重试)。"""


# ---------- 全局串行锁:163 对并发敏感 ----------

imap_lock = threading.Lock()


# ---------- 可替换的连接工厂(测试注入用) ----------

def default_connect(host: str, port: int, timeout: int):
    ctx = ssl.create_default_context()
    return imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=timeout)


connect_factory = default_connect


# ---------- 163 ID 命令 ----------

def send_163_id_command(conn) -> None:
    """LOGIN 后、SELECT 前发 ID 命令,否则 163 拒绝 SELECT。"""
    imaplib.Commands["ID"] = ("AUTH", "SELECTED", "NONAUTH", "AUTHENTICATED")
    client_id = (
        "name", "store_mail_web",
        "version", "0.1.0",
        "vendor", "store_mail_web",
        "support-email", "noreply@local",
    )
    typ, _ = conn._simple_command("ID", '("' + '" "'.join(client_id) + '")')
    if typ != "OK":
        raise ImapError(f"163 ID 命令失败: {typ}")


# ---------- 头部/正文解码(沿用 v1 四轮迭代后的稳定方案) ----------

def decode_header(raw: Optional[str]) -> str:
    """解码邮件头(=?GBK?B?...?= 等编码段)。"""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def strip_html(html: str) -> str:
    """把 HTML 邮件清洗成干净纯文本。

    亚马逊邮件特点:head 全是 CSS,body 里图片/链接是 <a href><img></a>,
    URL 藏在标签属性里,单纯去标签会残留。先剥属性再去标签。
    """
    body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.IGNORECASE | re.DOTALL)
    if body_match:
        html = body_match.group(1)
    html = re.sub(r"<(style|script|head)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
    html = re.sub(r'\s(?:href|src|cite|background|action|data-src|longdesc)="[^"]*"', "", html, flags=re.IGNORECASE)
    html = re.sub(r"\s(?:href|src|cite|background|action)='[^']*'", "", html, flags=re.IGNORECASE)
    html = re.sub(r'<img[^>]*alt="([^"]*)"[^>]*>', r" \1 ", html, flags=re.IGNORECASE)
    html = re.sub(r"<img[^>]*>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[\u200b-\u200f\u2028-\u202f\u205f\ufeff\u00a0]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_plain_text(text: str) -> str:
    """清洗 text/plain 正文(亚马逊的 text/plain 常是 Markdown 链接格式)。"""
    text = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "", text)
    text = re.sub(r"[\u200b-\u200f\u2028-\u202f\u205f\ufeff\u00a0]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_bodies(msg) -> tuple[str, Optional[str]]:
    """提取 (清洗后纯文本, 原始HTML或None)。

    策略:优先 text/plain;若太短(<30 字符,如亚马逊广告邮件的
    text/plain 就是字面量 "{}")则退取 text/html 清洗。
    """
    plain_text = None
    html_text: Optional[str] = None

    def _decode_part(part) -> Optional[str]:
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                return None
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except Exception:
            return None

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype == "text/plain" and plain_text is None:
                plain_text = _decode_part(part)
            elif ctype == "text/html" and html_text is None:
                html_text = _decode_part(part)
    else:
        decoded = _decode_part(msg)
        if decoded is not None:
            if msg.get_content_type() == "text/html":
                html_text = decoded
            else:
                plain_text = decoded

    if html_text is not None and len(html_text) > BODY_HTML_MAX:
        html_text = html_text[:BODY_HTML_MAX]

    if plain_text and len(plain_text.strip()) >= 30:
        text = clean_plain_text(plain_text)[:BODY_TEXT_MAX]
        return text, html_text
    if html_text:
        return strip_html(html_text)[:BODY_TEXT_MAX], html_text
    if plain_text:
        return clean_plain_text(plain_text)[:BODY_TEXT_MAX], html_text
    return "", html_text


# ---------- INTERNALDATE 解析(自带英文月份表,避免 locale 依赖) ----------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_INTERNALDATE_RE = re.compile(
    r'INTERNALDATE\s+"(\d{1,2})-([A-Za-z]{3})-(\d{4})\s+(\d{2}):(\d{2}):(\d{2})\s+([+-]\d{4})"'
)
_FLAGS_RE = re.compile(r"FLAGS\s+\(([^)]*)\)")
_SIZE_RE = re.compile(r"RFC822\.SIZE\s+(\d+)")


def parse_internaldate(meta: str) -> Optional[datetime]:
    match = _INTERNALDATE_RE.search(meta)
    if not match:
        return None
    day, mon, year, hh, mm, ss, offset = match.groups()
    month = _MONTHS.get(mon.lower())
    if not month:
        return None
    sign = 1 if offset[0] == "+" else -1
    tz = timezone(timedelta(
        hours=sign * int(offset[1:3]), minutes=sign * int(offset[3:5])
    ))
    try:
        return datetime(int(year), month, int(day), int(hh), int(mm), int(ss), tzinfo=tz)
    except ValueError:
        return None


def parse_flags(meta: str) -> set[str]:
    match = _FLAGS_RE.search(meta)
    if not match:
        return set()
    return {flag.strip().upper() for flag in match.group(1).split() if flag.strip()}


# ---------- 会话 ----------

class ImapSession:
    """单账号一次 IMAP 会话。用 with 持有,内部持有全局串行锁。

    测试通过替换模块级 connect_factory 注入假连接。
    """

    def __init__(self, host: str, port: int, mailbox: str, auth_code: str,
                 timeout: int = 30):
        self._host = host
        self._port = port
        self._mailbox = mailbox
        self._auth_code = auth_code
        self._timeout = timeout
        self._conn = None
        self._lock_held = False

    def __enter__(self) -> "ImapSession":
        imap_lock.acquire()
        self._lock_held = True
        try:
            conn = connect_factory(self._host, self._port, self._timeout)
            try:
                conn.login(self._mailbox, self._auth_code)
            except imaplib.IMAP4.error as exc:
                msg = str(exc).lower()
                if "auth" in msg or "password" in msg or "login" in msg:
                    raise ImapAuthError(f"登录失败(授权码可能失效): {self._mailbox}: {exc}") from exc
                raise ImapNetworkError(f"登录网络错误: {self._mailbox}: {exc}") from exc
            except (imaplib.IMAP4.abort, OSError, socket.timeout, TimeoutError) as exc:
                raise ImapNetworkError(f"连接异常: {self._mailbox}: {exc}") from exc
            send_163_id_command(conn)
            typ, data = conn.select("INBOX")
            if typ != "OK":
                raise ImapError(f"SELECT INBOX 失败: {typ} (mailbox={self._mailbox})")
            self._conn = conn
            return self
        except Exception:
            self._close_conn()
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        self._close_conn()

    def _close_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None
        if self._lock_held:
            imap_lock.release()
            self._lock_held = False

    # ---------- 查询 ----------

    def exists_count(self) -> int:
        """INBOX 现有邮件数(SELECT 返回的 EXISTS)。"""
        typ, data = self._conn.select("INBOX")
        if typ == "OK" and data and data[0]:
            try:
                return int(data[0])
            except (TypeError, ValueError):
                return 0
        return 0

    def search_uids_after(self, last_uid: int) -> list[int]:
        """返回 UID > last_uid 的全部 UID(升序)。"""
        typ, data = self._conn.uid("search", None, "UID", f"{last_uid + 1}:*")
        if typ != "OK":
            raise ImapError(f"UID SEARCH 失败: {typ}")
        uids: list[int] = []
        if data and data[0]:
            uids = [int(u) for u in data[0].split()]
        return sorted(u for u in uids if u > last_uid)

    def search_uids_since_days(self, days: int) -> list[int]:
        """返回最近 N 天内 INTERNALDATE 的全部 UID(升序)。"""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        month_names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        criteria = "SINCE {:02d}-{}-{:04d}".format(
            since.day, month_names[since.month - 1], since.year
        )
        typ, data = self._conn.uid("search", None, criteria)
        if typ != "OK":
            raise ImapError(f"SEARCH {criteria} 失败: {typ}")
        uids: list[int] = []
        if data and data[0]:
            uids = [int(u) for u in data[0].split()]
        return sorted(uids)

    # ---------- 单封操作 ----------

    def fetch_mail(self, uid: int) -> Optional[FetchedMail]:
        """拉一封完整邮件。BODY.PEEK 不置 \\Seen。不存在返回 None。"""
        typ, data = self._conn.uid(
            "fetch", str(uid), "(BODY.PEEK[] INTERNALDATE FLAGS RFC822.SIZE)"
        )
        if typ != "OK" or not data or not data[0]:
            return None
        first = data[0]
        if not isinstance(first, tuple) or len(first) < 2:
            return None
        meta = first[0].decode("ascii", errors="replace")
        raw = first[1]

        msg = email.message_from_bytes(raw)
        subject = decode_header(msg.get("Subject", ""))
        from_name_raw, from_addr = email.utils.parseaddr(decode_header(msg.get("From", "")))
        to_addr = email.utils.parseaddr(decode_header(msg.get("To", "")))[1]
        internal_dt = parse_internaldate(meta)
        flags = parse_flags(meta)
        size_match = _SIZE_RE.search(meta)
        size = int(size_match.group(1)) if size_match else len(raw)

        received_ts: int
        if internal_dt is not None:
            received_ts = int(internal_dt.timestamp())
        else:
            parsed = email.utils.parsedate_to_datetime(msg.get("Date", ""))
            received_ts = (
                int(parsed.timestamp())
                if parsed is not None
                else int(time.time())
            )

        body_text, body_html = extract_bodies(msg)
        message_id = (msg.get("Message-ID") or "").strip().strip("<>")

        return FetchedMail(
            uid=uid,
            message_id=message_id,
            subject=subject,
            from_name=from_name_raw or from_addr,
            from_addr=from_addr,
            to_addr=to_addr,
            internal_date=(
                internal_dt.astimezone(timezone.utc).isoformat(timespec="seconds")
                if internal_dt is not None else None
            ),
            received_ts=received_ts,
            size_bytes=size,
            unread="\\SEEN" not in flags,
            body_text=body_text,
            body_html=body_html,
            preview=(body_text or "")[:200],
        )

    def mark_seen(self, uid: int) -> bool:
        """回写 \\Seen(用户主动打开邮件时用)。尽力而为。"""
        typ, _ = self._conn.uid("store", str(uid), "+FLAGS", "(\\Seen)")
        return typ == "OK"


# ---------- 连接测试 ----------

def test_connection(host: str, port: int, mailbox: str, auth_code: str,
                    timeout: int = 30) -> dict:
    """登录 + SELECT 探活。返回 {ok, exists, error}。"""
    try:
        with ImapSession(host, port, mailbox, auth_code, timeout) as session:
            return {"ok": True, "exists": session.exists_count(), "error": None}
    except ImapAuthError as exc:
        return {"ok": False, "exists": None, "error": str(exc)}
    except ImapError as exc:
        return {"ok": False, "exists": None, "error": str(exc)}
    except (OSError, socket.timeout, TimeoutError) as exc:
        return {"ok": False, "exists": None, "error": f"网络错误: {exc}"}
