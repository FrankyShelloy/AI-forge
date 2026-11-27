"""Real SMTP + IMAP Human-in-the-Loop service for Quality Service escalation & FAQ learning."""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
import ssl
import threading
from email.header import decode_header
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from typing import Any

import aiosmtplib
from email_reply_parser import EmailReplyParser

from .config import Settings
from .db import Database
from .retrieval import HybridRetriever

logger = logging.getLogger(__name__)

TICKET_CODE_RE = re.compile(r"\[?(QA-\d{4}-\d{4})\]?", re.I)

# Russian + English quote / signature cutoffs (including Roundcube RU/EN formats)
RU_QUOTE_PATTERNS = [
    re.compile(r"^\s*\d{1,4}[./-]\d{1,2}[./-]\d{2,4}.*?(?:написал|пишет|wrote).*?:", re.I | re.M),
    re.compile(r"^\s*On\s+.+?wrote:\s*$", re.I | re.M),
    re.compile(r"^\s*[-_]{2,}\s*(?:Исходное сообщение|Original Message|Пересылаемое сообщение)", re.I | re.M),
    re.compile(r"^\s*(?:От|From):\s+.+\n\s*(?:Отправлено|Sent|Дата|Date):", re.I | re.M),
    re.compile(r"^\s*(?:С уважением|Best regards|Kind regards|Regards),?\s*$", re.I | re.M),
    re.compile(r"^\s*--\s*$", re.M),
]


def _decode_hdr(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(chunk))
    return "".join(out)


def _extract_plain_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        html_fallback = ""
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ctype == "text/plain":
                return text
            if ctype == "text/html" and not html_fallback:
                html_fallback = re.sub(r"<[^>]+>", " ", text)
        return html_fallback
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def clean_expert_reply(raw_text: str) -> str:
    """Strip email quotes, forwarded history, and RU/EN signatures from expert reply."""
    if not raw_text:
        return ""
    # First run email-reply-parser
    parsed = EmailReplyParser.parse_reply(raw_text).strip()
    text = parsed or raw_text.strip()

    # Strip quoted lines starting with '>'
    lines = []
    for ln in text.splitlines():
        if ln.lstrip().startswith(">"):
            break
        lines.append(ln)
    text = "\n".join(lines).strip()

    # Apply RU/EN quote & signature cutoffs
    for pat in RU_QUOTE_PATTERNS:
        m = pat.search(text)
        if m:
            text = text[: m.start()].strip()

    return re.sub(r"\n{3,}", "\n\n", text).strip()


class EmailHITLService:
    def __init__(self, settings: Settings, db: Database, retriever: HybridRetriever) -> None:
        self.settings = settings
        self.db = db
        self.retriever = retriever
        self._imap_lock = threading.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def send_escalation(
        self,
        *,
        session_id: str,
        user_email: str,
        question: str,
        context_summary: str,
        directorate: str | None = None,
    ) -> dict[str, Any]:
        """Create escalation ticket and send a real SMTP email to Quality Service with user in CC."""
        msg_id = make_msgid(domain="corp.local")
        ticket = self.db.create_ticket(
            session_id=session_id,
            user_email=user_email or self.settings.default_employee_email,
            question=question,
            context_summary=context_summary,
            directorate=directorate,
            smtp_message_id=msg_id,
            status="sending",
        )
        tcode = ticket["ticket_code"]

        em = EmailMessage()
        em["From"] = self.settings.bot_email
        em["To"] = self.settings.quality_email
        cc_addr = user_email or self.settings.default_employee_email
        if cc_addr and cc_addr.lower() != self.settings.quality_email.lower():
            em["Cc"] = cc_addr
        em["Reply-To"] = self.settings.bot_email
        em["Message-ID"] = msg_id
        em["Subject"] = f"[{tcode}] Запрос в Службу качества: {question[:72]}"

        body = (
            f"Добрый день, коллеги из Службы качества!\n\n"
            f"ИИ-агент по корпоративным регламентам не смог дать подтверждённый ответ на вопрос сотрудника "
            f"(во избежание недостоверной информации обращение передано эксперту).\n\n"
            f"Номер обращения: {tcode}\n"
            f"Сотрудник (в копии): {cc_addr}\n"
            f"Подразделение / контекст: {directorate or 'не уточнено'}\n\n"
            f"Вопрос сотрудника:\n{question}\n\n"
        )
        if context_summary:
            body += f"Краткий контекст диалога:\n{context_summary}\n\n"
        body += (
            "Пожалуйста, ответьте на это письмо («Ответить всем» или «Ответить» боту). "
            "После получения ответа от уполномоченного адреса Службы качества агент автоматически "
            "сохранит ваше разъяснение в проверенной базе знаний (FAQ) для повторного использования.\n"
        )
        em.set_content(body)

        smtp_error: str | None = None
        try:
            await aiosmtplib.send(
                em,
                hostname=self.settings.smtp_host,
                port=self.settings.smtp_port,
                username=self.settings.bot_email or None,
                password=self.settings.bot_email_password or None,
                start_tls=self.settings.smtp_use_tls,
                use_tls=self.settings.smtp_use_ssl,
                validate_certs=self.settings.mail_validate_certs,
                timeout=12,
            )
            status = "sent"
            logger.info("Escalation email %s sent via SMTP to %s (CC: %s)", tcode, self.settings.quality_email, cc_addr)
        except Exception as exc:
            smtp_error = str(exc)
            status = "smtp_error"
            logger.error("SMTP send failed for %s: %s", tcode, exc)

        # Update status if changed
        with self.db._lock, self.db._connect() as conn:
            conn.execute("UPDATE escalation_tickets SET status = ? WHERE ticket_code = ?", (status, tcode))

        ticket["status"] = status
        ticket["smtp_error"] = smtp_error
        return ticket

    def _poll_imap_sync(self) -> dict[str, Any]:
        """Synchronously check IMAP INBOX for verified Quality Service replies (serialized via lock)."""
        if not self._imap_lock.acquire(blocking=False):
            return {"checked": 0, "processed": 0, "ignored_unauth": 0, "error": None, "skipped_busy": True}
        try:
            return self._poll_imap_locked()
        finally:
            self._imap_lock.release()

    def _poll_imap_locked(self) -> dict[str, Any]:
        processed = 0
        ignored_unauth = 0
        checked = 0
        errors: list[str] = []

        try:
            ssl_ctx = (
                ssl.create_default_context()
                if self.settings.mail_validate_certs
                else ssl._create_unverified_context()
            )
            if self.settings.imap_use_ssl:
                mail = imaplib.IMAP4_SSL(
                    self.settings.imap_host, self.settings.imap_port, ssl_context=ssl_ctx
                )
            else:
                mail = imaplib.IMAP4(self.settings.imap_host, self.settings.imap_port)
                try:
                    mail.starttls(ssl_context=ssl_ctx)
                except Exception:
                    pass
            mail.login(self.settings.bot_email, self.settings.bot_email_password)
            mail.select("INBOX")
        except Exception as exc:
            return {"checked": 0, "processed": 0, "ignored_unauth": 0, "error": str(exc)}

        try:
            typ, data = mail.search(None, "UNSEEN")
            if typ != "OK" or not data or not data[0]:
                mail.logout()
                return {"checked": 0, "processed": 0, "ignored_unauth": 0, "error": None}

            msg_nums = data[0].split()
            for num in msg_nums:
                checked += 1
                typ2, msg_data = mail.fetch(num, "(RFC822)")
                if typ2 != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                raw_bytes = msg_data[0][1]
                msg = email.message_from_bytes(raw_bytes)

                sender_raw = _decode_hdr(msg.get("From"))
                _, sender_email = parseaddr(sender_raw)
                sender_email = sender_email.lower().strip()
                subject = _decode_hdr(msg.get("Subject"))
                in_reply_to = _decode_hdr(msg.get("In-Reply-To"))
                references = _decode_hdr(msg.get("References"))

                # Match ticket by [QA-XXXX] in subject OR In-Reply-To / References Message-ID
                m_code = TICKET_CODE_RE.search(subject)
                tcode = m_code.group(1).upper() if m_code else None
                ref_ids = [x.strip() for x in f"{in_reply_to} {references}".split() if x.strip()]

                ticket = self.db.find_ticket_by_message_ids_or_code(ref_ids, tcode)
                if not ticket:
                    # Also check body for ticket code as last resort
                    raw_body = _extract_plain_body(msg)
                    m_body = TICKET_CODE_RE.search(raw_body)
                    if m_body:
                        ticket = self.db.find_ticket_by_message_ids_or_code([], m_body.group(1).upper())
                else:
                    raw_body = _extract_plain_body(msg)

                if not ticket:
                    # Mark unmatched/system emails as Seen so IMAP doesn't re-fetch them every 10s forever
                    mail.store(num, "+FLAGS", "\\Seen")
                    continue

                # Security whitelist check: ONLY authorized Quality Service addresses can enrich KB!
                whitelist = self.settings.whitelist_set
                if whitelist and sender_email not in whitelist:
                    logger.warning(
                        "Ignored unauthorized reply to %s from %s (not in whitelist %s)",
                        ticket["ticket_code"],
                        sender_email,
                        whitelist,
                    )
                    self.db.log_security_event(
                        event_type="unauthorized_email_reply",
                        actor=sender_email,
                        ticket_code=ticket["ticket_code"],
                        detail="Попытка ответа в тред обращения с адреса вне whitelist Службы качества — письмо отклонено, БЗ не изменена.",
                    )
                    ignored_unauth += 1
                    mail.store(num, "+FLAGS", "\\Seen")
                    continue

                clean_text = clean_expert_reply(raw_body)
                if len(clean_text) < 5:
                    mail.store(num, "+FLAGS", "\\Seen")
                    continue

                was_already_indexed = ticket.get("status") == "indexed"
                faq = self.db.upsert_faq_for_ticket(
                    ticket_code=ticket["ticket_code"],
                    question=ticket["question"],
                    answer=clean_text,
                    verified_by=sender_email,
                    directorate=ticket.get("directorate") or "ALL",
                    linked_doc_title=f"Ответ Службы качества по обращению {ticket['ticket_code']}",
                )
                try:
                    self.retriever.index_faq_entry(faq)
                    self.db.mark_ticket_answered(
                        ticket_code=ticket["ticket_code"],
                        expert_email=sender_email,
                        raw_reply=raw_body,
                        clean_reply=clean_text,
                        faq_id=faq["id"],
                        linked_doc_title=faq["linked_doc_title"],
                    )
                    header_lbl = (
                        "Обновлён проверенный ответ от Службы качества"
                        if was_already_indexed
                        else "Получен проверенный ответ от Службы качества"
                    )
                    self.db.add_message(
                        session_id=ticket["session_id"],
                        role="assistant",
                        content=(
                            f"**{header_lbl}** (`{sender_email}`, обращение `{ticket['ticket_code']}`):\n\n"
                            f"{clean_text}\n\n"
                            f"_Разъяснение сохранено в проверенной базе знаний (FAQ) и будет автоматически использоваться при повторных вопросах._"
                        ),
                        outcome="faq_updated",
                        ticket_code=ticket["ticket_code"],
                    )
                    mail.store(num, "+FLAGS", "\\Seen")
                    processed += 1
                    logger.info("Indexed verified Quality reply for %s from %s", ticket["ticket_code"], sender_email)
                except Exception as exc:
                    errors.append(f"{ticket['ticket_code']}: {exc}")
                    logger.error("Failed to index FAQ for %s: %s", ticket["ticket_code"], exc)

            mail.logout()
        except Exception as exc:
            errors.append(str(exc))
            try:
                mail.logout()
            except Exception:
                pass

        return {
            "checked": checked,
            "processed": processed,
            "ignored_unauth": ignored_unauth,
            "error": "; ".join(errors) if errors else None,
        }

    async def sync_inbox_now(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._poll_imap_sync)

    async def _poll_loop(self) -> None:
        logger.info("IMAP HITL worker started (interval=%ss)", self.settings.imap_poll_interval_seconds)
        while not self._stop_event.is_set():
            try:
                res = await self.sync_inbox_now()
                if res.get("processed", 0) > 0:
                    logger.info("IMAP poll indexed %d new Quality answers", res["processed"])
            except Exception as exc:
                logger.debug("IMAP poll tick error: %s", exc)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=max(3, self.settings.imap_poll_interval_seconds)
                )
            except asyncio.TimeoutError:
                pass

    def start_background_worker(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._poll_loop())

    async def stop_background_worker(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
