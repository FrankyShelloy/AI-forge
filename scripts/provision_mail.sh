#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${MAIL_CONTAINER:-ai-forge-mail-1}"
MS="docker exec ${CONTAINER} /opt/admin/bin/mailserver"

$MS domain:create corp.local || true
$MS email:create bot@corp.local "${BOT_EMAIL_PASSWORD:-BotPass2026!}" "ИИ-агент Софтлайн" || true
$MS email:create quality@corp.local "${QUALITY_EMAIL_PASSWORD:-QualityPass2026!}" "Служба качества Софтлайн" || true
$MS email:create employee@corp.local "${EMPLOYEE_EMAIL_PASSWORD:-EmployeePass2026!}" "Сотрудник Софтлайн" || true
$MS email:admin quality@corp.local || true
$MS email:list
