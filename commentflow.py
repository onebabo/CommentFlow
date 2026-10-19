"""즐겨찾기 작품 댓글 초안 도우미.

초안 작성 → 규정 검사 → 클립보드 복사 + 회차 페이지 열기까지만 합니다.
사이트 등록 버튼은 사용자가 직접 누릅니다.
즐겨찾기 작품은 book_web_Crawler 의 book_work_list (used = 'Y') 기준입니다.
"""
import argparse
import sys
import webbrowser
from urllib.parse import urlparse

import pymysql.cursors

import recommend
import rules
from db import get_connection

CLOSINGS = ["", " 다음 화도 기대하겠습니다.", " 앞으로 어떻게 전개될지 궁금하네요."]
STATUS_NAMES = {"D": "초안", "C": "복사함", "P": "등록완료"}


def book_id_from_url(url: str) -> int:
    """회차 URL 에서 소설 아이디를 구합니다. 예: https://toki35.com/novel/59094/6063116 → 59094"""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) < 2 or not parts[1].isdigit():
        raise SystemExit(f"회차 URL 형식이 아닙니다: {url}")
    return int(parts[1])


def find_favorite(cursor, book_id: int) -> dict | None:
    cursor.execute(
        "SELECT book_id, title FROM book_work_list WHERE book_id = %s AND used = 'Y'",
        (book_id,),
    )
    return cursor.fetchone()


def get_comment(cursor, comment_seq: int) -> dict:
    cursor.execute(
        """
        SELECT c.*, COALESCE(w.title, '(즐겨찾기 해제됨)') AS title
          FROM book_comment c
          LEFT JOIN book_work_list w ON w.book_id = c.book_id
         WHERE c.comment_seq = %s
        """,
        (comment_seq,),
    )
    row = cursor.fetchone()
    if not row:
        raise SystemExit(f"초안 #{comment_seq} 이 없습니다.")
    return row


def print_check(text: str) -> bool:
    errors, warnings = rules.check(text)
    for e in errors:
        print(f"    [불가] {e}")
    for w in warnings:
        print(f"    [주의] {w}")
    return not errors


def make_candidates(memo: str) -> list[str]:
    base = memo.strip()
    if not base:
        raise SystemExit("감상 메모가 비어 있습니다.")
    if base[-1] not in ".!?~":
        base += "."
    return [base + closing for closing in CLOSINGS]


def copy_to_clipboard(text: str) -> None:
    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()
    root.destroy()


def cmd_works(args) -> None:
    with get_connection() as conn, conn.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(
            "SELECT book_id, title, last_episode_no FROM book_work_list "
            "WHERE used = 'Y' ORDER BY sort_no, book_id"
        )
        for w in cursor.fetchall():
            print(f"{w['book_id']:<8} {w['title']}  (최신 {w['last_episode_no'] or '-'}화)")


def cmd_draft(args) -> None:
    book_id = book_id_from_url(args.episode_url)
    with get_connection() as conn, conn.cursor(pymysql.cursors.DictCursor) as cursor:
        work = find_favorite(cursor, book_id)
        if not work:
            raise SystemExit(f"즐겨찾기(book_work_list)에 없는 작품입니다: {book_id}")

        print(f"작품: {work['title']}\n")
        candidates = make_candidates(args.memo)
        for i, text in enumerate(candidates, 1):
            print(f"  {i}) {text}")
            print_check(text)
        print("\n번호 선택 / e: 직접 입력 / 그 외: 취소")
        choice = input("> ").strip()
        if choice.lower() == "e":
            body = input("댓글 입력> ").strip()
        elif choice.isdigit() and 1 <= int(choice) <= len(candidates):
            body = candidates[int(choice) - 1]
        else:
            print("취소했습니다.")
            return

        if not print_check(body):
            raise SystemExit("규정에 맞지 않아 저장하지 않았습니다.")

        cursor.execute(
            "SELECT comment_seq FROM book_comment WHERE body = %s AND status = 'P' LIMIT 1", (body,)
        )
        if cursor.fetchone():
            print("    [주의] 같은 내용으로 등록한 기록이 있습니다.")

        cursor.execute(
            "INSERT INTO book_comment (book_id, episode_url, memo, body) VALUES (%s, %s, %s, %s)",
            (book_id, args.episode_url, args.memo, body),
        )
        print(f"초안 #{cursor.lastrowid} 저장. 등록하려면: python commentflow.py copy {cursor.lastrowid}")


def cmd_copy(args) -> None:
    with get_connection() as conn, conn.cursor(pymysql.cursors.DictCursor) as cursor:
        row = get_comment(cursor, args.id)
        if row["status"] == "P":
            raise SystemExit(f"초안 #{args.id} 은 이미 등록 완료 상태입니다.")
        if not print_check(row["body"]):
            raise SystemExit("규정에 맞지 않는 초안입니다.")

        copy_to_clipboard(row["body"])
        webbrowser.open(row["episode_url"])
        cursor.execute("UPDATE book_comment SET status = 'C' WHERE comment_seq = %s", (args.id,))
        conn.commit()

        print(f"[{row['title']}] 댓글을 클립보드에 복사하고 회차 페이지를 열었습니다.")
        print(f"  {row['body']}")
        print("\n페이지에서 붙여넣고 직접 등록하세요.")
        answer = input("등록을 마쳤으면 y, 아직이면 Enter> ").strip().lower()
        if answer == "y":
            cursor.execute(
                "UPDATE book_comment SET status = 'P', posted_at = NOW() WHERE comment_seq = %s",
                (args.id,),
            )
            print("등록 완료로 기록했습니다.")


def cmd_list(args) -> None:
    sql = """
        SELECT c.*, COALESCE(w.title, '(즐겨찾기 해제됨)') AS title
          FROM book_comment c
          LEFT JOIN book_work_list w ON w.book_id = c.book_id
    """
    params: tuple = ()
    if args.status:
        sql += " WHERE c.status = %s"
        params = (args.status,)
    with get_connection() as conn, conn.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(sql + " ORDER BY c.comment_seq DESC LIMIT %s", params + (args.limit,))
        for c in cursor.fetchall():
            status = STATUS_NAMES.get(c["status"], c["status"])
            print(f"#{c['comment_seq']:<4} [{status}] {c['title']}  {c['episode_url']}")
            print(f"       {c['body']}")


def cmd_recommend(args) -> None:
    # TEMP: --count 3 인 경우 임시로 5로 변경
    count = 5 if args.count == 3 else args.count
    with get_connection() as conn:
        recommend.run(conn, args.kind, count, args.work_id)


def cmd_check(args) -> None:
    ok = print_check(args.text)
    print("규정 통과" if ok else "규정 위반")


def main() -> None:
    parser = argparse.ArgumentParser(description="즐겨찾기 작품 댓글 초안 도우미")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("works", help="즐겨찾기 작품 목록 (book_work_list)")
    p.set_defaults(func=cmd_works)

    p = sub.add_parser("draft", help="감상 메모로 댓글 초안 작성")
    p.add_argument("episode_url")
    p.add_argument("--memo", required=True, help="직접 읽고 느낀 감상")
    p.set_defaults(func=cmd_draft)

    p = sub.add_parser("copy", help="초안을 클립보드에 복사하고 회차 페이지 열기")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_copy)

    p = sub.add_parser("list", help="초안/등록 기록")
    p.add_argument("--status", choices=list(STATUS_NAMES), help="D:초안, C:복사함, P:등록완료")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("recommend", help="회차 내용 분석 → 추천 댓글 생성 (book_web /comment_list [댓글 생성])")
    p.add_argument("--kind", default="novel", choices=["novel", "toon", "manhwa"])
    p.add_argument("--count", type=int, default=20,
                   help="추천 댓글이 없는 작품 중 랜덤 선정 개수 (--book-id 를 주면 그 작품의 회차 개수)")
    p.add_argument("--book-id", "--work-id", dest="work_id", type=int,
                   help="이 작품(book_id / toon_id / manhwa_id)만 대상으로 생성. 없으면 전체 대상")
    p.set_defaults(func=cmd_recommend)

    p = sub.add_parser("check", help="댓글 문구 규정 검사만")
    p.add_argument("text")
    p.set_defaults(func=cmd_check)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
