"""Read student assignments and prepare or acknowledge deadline reminders."""

import argparse
import hashlib
import json
import msvcrt
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urljoin

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from canvas_session import (
    DATA, ORIGIN, CheckError, NeedsLogin, account_id, atomic_write, canvas_state,
    get_json, load_session, save_session,
)


BEIJING = timezone(timedelta(hours=8))
LOOKAHEAD = timedelta(days=7)
PENDING = DATA / "pending.json"


def read_json(path):
    return json.loads(path.read_bytes()) if path.exists() else {}


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, sort_keys=True).encode())


@contextmanager
def check_lock():
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "check.lock").open("a+b") as stream:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise CheckError("另一次检查还在进行，请稍后重试。") from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def pages(api, path, params):
    url = ORIGIN + path + "?" + urlencode(dict(params, per_page=100))
    seen = set()
    while url:
        if url in seen:
            raise CheckError("Canvas 返回了循环分页链接。")
        seen.add(url)
        rows, links = get_json(api, url)
        if not isinstance(rows, list):
            raise CheckError("Canvas 列表格式异常。")
        yield from rows
        match = re.search(r'<([^>]+)>\s*;\s*rel="next"', links, re.I)
        url = urljoin(url, match.group(1)) if match else None


def assignment_item(course, assignment, now):
    if assignment.get("published") is False or not assignment.get("due_at"):
        return
    submission = assignment.get("submission") or {}
    if submission.get("excused") or submission.get("submitted_at"):
        return
    state = submission.get("workflow_state")
    if state in {"submitted", "pending_review"} or (state == "graded" and not submission.get("missing")):
        return
    due = datetime.fromisoformat(assignment["due_at"])
    if due.utcoffset() is None:
        raise CheckError("作业截止时间缺少时区。")
    due = due.astimezone(BEIJING)
    remaining = due - now
    if not timedelta(0) < remaining <= LOOKAHEAD:
        return
    stage = "within_24h" if remaining <= timedelta(hours=24) else "upcoming"
    course_id, assignment_id = int(course["id"]), int(assignment["id"])
    return {
        "key": f"{course_id}/{assignment_id}",
        "course": course.get("name") or course.get("course_code") or str(course_id),
        "title": assignment["name"],
        "due": due.isoformat(timespec="seconds"),
        "stage": stage,
        "url": f"{ORIGIN}/courses/{course_id}/assignments/{assignment_id}",
    }


def collect(api, now):
    courses = list(pages(api, "/api/v1/courses", {
        "enrollment_type": "student", "enrollment_state": "active", "state[]": "available",
    }))
    items, errors = {}, []
    for course in courses:
        course_id = int(course["id"])
        try:
            rows = pages(api, f"/api/v1/courses/{course_id}/assignments", {
                "include[]": "submission", "override_assignment_dates": "true",
            })
            for row in rows:
                item = assignment_item(course, row, now)
                if item:
                    items[item["key"]] = item
        except (CheckError, PlaywrightError) as exc:
            detail = str(exc) if isinstance(exc, CheckError) else "连接失败或超时。"
            errors.append({"course": course.get("name") or str(course_id), "detail": detail})
    return sorted(items.values(), key=lambda i: (i["due"], i["key"])), errors, len(courses)


def reminders(items, previous):
    alerts = []
    for item in items:
        old = previous.get(item["key"])
        current = [item["due"], item["stage"]]
        if old == current:
            continue
        reason = "首次发现" if old is None else "截止状态变化"
        if old and old[0] != item["due"]:
            reason = "截止时间变更"
        alerts.append(dict(item, reason=reason))
    return alerts


def ledger(owner):
    return DATA / f"notified-{int(owner)}.json"


def prepare(owner, items, errors):
    history = read_json(ledger(owner))
    alerts = reminders(items, history)
    # Preserve unseen rows if one or more courses could not be read in full.
    snapshot = dict(history) if errors else {}
    snapshot.update({i["key"]: [i["due"], i["stage"]] for i in items})
    if not alerts:
        if snapshot != history:
            write_json(ledger(owner), snapshot)
        PENDING.unlink(missing_ok=True)
        return [], None
    pending = {"owner": owner, "snapshot": snapshot}
    receipt = hashlib.sha256(json.dumps(pending, sort_keys=True).encode()).hexdigest()
    write_json(PENDING, dict(pending, receipt=receipt))
    return alerts, receipt


def acknowledge(receipt):
    pending = read_json(PENDING)
    if pending.get("receipt") != receipt:
        raise CheckError("提醒批次已更新，请重新检查。")
    write_json(ledger(pending["owner"]), pending["snapshot"])
    PENDING.unlink()
    return {"status": "acknowledged"}


def check(scheduled):
    original = load_session()
    with sync_playwright() as runner:
        api = runner.request.new_context(storage_state=original)
        try:
            owner = account_id(api)
            now = datetime.now(BEIJING)
            items, errors, count = collect(api, now)
            fresh = canvas_state(api.storage_state())
            if fresh != original and load_session() == original:
                save_session(fresh)
        finally:
            api.dispose()
    receipt = None
    if scheduled:
        items, receipt = prepare(owner, items, errors)
    return {
        "status": "partial" if errors else "ok",
        "checked_at": now.isoformat(timespec="seconds"), "courses": count,
        "items": items, "errors": errors, "receipt": receipt,
    }


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--scheduled", action="store_true")
    group.add_argument("--ack", metavar="RECEIPT")
    args = parser.parse_args()
    try:
        with check_lock():
            result = acknowledge(args.ack) if args.ack else check(args.scheduled)
    except NeedsLogin as exc:
        result = {"status": "login_required", "message": str(exc)}
    except (CheckError, OSError, ValueError, KeyError, TypeError, PlaywrightError) as exc:
        detail = str(exc) if isinstance(exc, CheckError) else "网络或本地数据读取失败，请重试。"
        result = {"status": "error", "message": detail}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"ok", "acknowledged"} else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
