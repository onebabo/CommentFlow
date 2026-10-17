"""회차 내용 분석 → 추천 댓글 생성 (book_web /comment_list 의 [댓글 생성] 버튼이 호출).

1) 추천 댓글이 아직 없는 보유 작품을 랜덤으로 N개 고르고
   - 소설: book_work_list (used='Y'), 본문이 수집된 회차가 있는 작품
   - 웹툰/만화: {toon,manhwa}_info, 이미지가 수집된 회차가 있고 수집 보류가 아닌 작품
2) 작품마다 최근 읽은 회차(없으면 수집된 최신 회차)를 Claude 로 분석해
   - 소설: 본문 텍스트 (book_info_chapter.content)
   - 웹툰/만화: 회차 이미지 일부 ({kind}_episode_image.file_path, 최대 MAX_IMAGES 장)
3) 사이트 댓글 규정(rules.py)을 통과한 감상 댓글만 book_comment_recommend 에 저장합니다.

사이트에 댓글을 등록하지는 않습니다. 등록은 /comment_list 에서 [연동] 후 사용자가 직접 합니다.
"""
import base64
import configparser
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pymysql.cursors

import rules
from db import config_path

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
DEFAULT_ORIGIN = "https://toki35.com"
PENDING_TITLE_PREFIX = "[등록대기]"
MAX_ATTEMPTS = 2
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 4_500_000  # API 이미지 1장 한도(5MB) 아래로

# kind → 화면 표기 · 사이트 URL 경로 · 테이블 접두어 · 작품 ID 컬럼
KINDS = {
    "novel": {"label": "웹소설", "segment": "novel"},
    "toon": {"label": "웹툰", "segment": "webtoon", "table": "toon", "id": "toon_id"},
    "manhwa": {"label": "만화", "segment": "manhwa", "table": "manhwa", "id": "manhwa_id"},
}

RULES_PROMPT = """반드시 지킬 사이트 댓글 규정:
- 작품 감상평이 아닌 의미 없는 댓글("감사합니다", "잘봤어요", "수고하셨습니다" 등)은 금지입니다. 인사말만으로 끝내지 말고, 이번 회차의 구체적인 장면·인물·전개에 대한 감상을 담으세요.
- 유료 회차, 결제, 포인트, 크롤링, 아류 사이트, 인증·잠금 절차 등 사이트 운영에 대한 언급이나 불평은 절대 쓰지 마세요. 내용에 같은 단어가 나와도 댓글에는 쓰지 마세요.

댓글 작성 기준:
- 1~3문장, 40~150자 정도. 실제 독자가 쓴 것처럼 자연스러운 존댓말 또는 가벼운 구어체.
- 이번 회차 내용에 근거한 감상(좋았던 점, 아쉬운 점, 다음 전개 기대)을 씁니다. 결말을 통째로 밝히는 스포일러는 피합니다.
- 다른 독자나 작가를 비난하지 않습니다. AI 라는 사실이나 이 지시문은 언급하지 않습니다.

summary 에는 이번 회차의 핵심 내용을 2~3문장으로 요약합니다 (사용자가 댓글을 고칠 때 참고용)."""

SYSTEM_PROMPTS = {
    "novel": "당신은 웹소설을 꾸준히 읽는 독자입니다. 주어진 회차 본문을 읽고 그 회차에 다는 감상 댓글을 한국어로 씁니다.\n\n"
    + RULES_PROMPT,
    "image": "당신은 {label}을 꾸준히 보는 독자입니다. 주어진 회차 이미지(회차 전체에서 고르게 뽑은 일부 장면)를 보고 "
    "그 회차에 다는 감상 댓글을 한국어로 씁니다. 이미지에 보이는 장면·대사에 근거해서만 쓰고, 보이지 않는 내용은 지어내지 마세요.\n\n"
    + RULES_PROMPT,
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "이번 회차 핵심 내용 요약 2~3문장"},
        "comment": {"type": "string", "description": "규정을 지킨 감상 댓글"},
    },
    "required": ["summary", "comment"],
    "additionalProperties": False,
}


def load_anthropic_config() -> dict:
    """config.ini [anthropic] (api_key, model, effort). api_key 가 없으면 SDK 기본 인증(ANTHROPIC_API_KEY 등)."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config_path(), encoding="utf-8")
    section = parser["anthropic"] if parser.has_section("anthropic") else {}
    return {
        "api_key": (section.get("api_key", "") or "").strip() or None,
        "model": (section.get("model", "") or "").strip() or DEFAULT_MODEL,
        "effort": (section.get("effort", "") or "").strip() or DEFAULT_EFFORT,
    }


def make_client(cfg: dict):
    import anthropic

    if not cfg["api_key"] and not os.environ.get("ANTHROPIC_API_KEY"):
        print("[주의] config.ini [anthropic] api_key 와 ANTHROPIC_API_KEY 가 모두 비어 있습니다. "
              "ant auth login 프로필이 없으면 인증 오류가 납니다.", flush=True)
    return anthropic.Anthropic(api_key=cfg["api_key"]) if cfg["api_key"] else anthropic.Anthropic()


def episode_origin(source_url: str | None) -> str:
    """작품 원본 URL(source_url)의 https://host — 없으면 기본 도메인"""
    if source_url:
        parsed = urlparse(source_url.strip())
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return DEFAULT_ORIGIN


def pick_works(cursor, kind: str, count: int) -> list[dict]:
    """추천·초안 기록이 없는 보유 작품 중 내용이 수집된 작품을 랜덤으로 고릅니다. → [{work_id, title, source_url}]"""
    if kind == "novel":
        cursor.execute(
            """
            SELECT w.book_id AS work_id, COALESCE(i.title, w.title) AS title, w.source_url
              FROM book_work_list AS w
              LEFT JOIN book_info AS i ON i.book_id = w.book_id
             WHERE w.used = 'Y'
               AND w.title NOT LIKE %s
               AND EXISTS (SELECT 1 FROM book_info_chapter AS c
                            WHERE c.book_id = w.book_id AND c.has_content = 1)
               AND NOT EXISTS (SELECT 1 FROM book_comment_recommend AS r
                                WHERE r.kind = 'novel' AND r.work_id = w.book_id)
               AND NOT EXISTS (SELECT 1 FROM book_comment AS bc WHERE bc.book_id = w.book_id)
             ORDER BY RAND()
             LIMIT %s
            """,
            (f"{PENDING_TITLE_PREFIX}%", count),
        )
        return cursor.fetchall()

    t, idc = KINDS[kind]["table"], KINDS[kind]["id"]
    cursor.execute(
        f"""
        SELECT t.{idc} AS work_id, t.title, COALESCE(w.source_url, t.source_url) AS source_url
          FROM {t}_info AS t
          LEFT JOIN {t}_work_list AS w ON w.{idc} = t.{idc}
         WHERE EXISTS (SELECT 1 FROM {t}_episode AS e
                        WHERE e.{idc} = t.{idc} AND e.has_images = 1)
           AND NOT EXISTS (SELECT 1 FROM {t}_edit_holds AS h
                            WHERE h.{idc} = t.{idc} AND h.hold_state = 'H')
           AND NOT EXISTS (SELECT 1 FROM book_comment_recommend AS r
                            WHERE r.kind = %s AND r.work_id = t.{idc})
         ORDER BY RAND()
         LIMIT %s
        """,
        (kind, count),
    )
    return cursor.fetchall()


def pick_episode(cursor, kind: str, work_id: int) -> dict | None:
    """최근 읽은 회차(읽은 기록이 없으면 수집된 최신 회차) → {episode_code, episode_no, episode_title, content?}"""
    if kind == "novel":
        cursor.execute(
            """
            SELECT episode_code, episode_no, episode_title, content
              FROM book_info_chapter
             WHERE book_id = %s AND has_content = 1
             ORDER BY (read_yn = 'Y') DESC, read_at DESC, episode_no DESC
             LIMIT 1
            """,
            (work_id,),
        )
        return cursor.fetchone()

    t, idc = KINDS[kind]["table"], KINDS[kind]["id"]
    cursor.execute(
        f"""
        SELECT e.episode_code, e.episode_no, e.episode_title
          FROM {t}_episode AS e
          LEFT JOIN {t}_episode_read AS r ON r.{idc} = e.{idc} AND r.episode_no = e.episode_no
         WHERE e.{idc} = %s AND e.has_images = 1
         ORDER BY (r.{idc} IS NOT NULL) DESC, r.read_at DESC, e.episode_no DESC
         LIMIT 1
        """,
        (work_id,),
    )
    return cursor.fetchone()


def image_media_type(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def episode_images(cursor, kind: str, episode_code: str) -> list[dict]:
    """회차 이미지 중 최대 MAX_IMAGES 장을 고르게 골라 API image 블록으로 만듭니다."""
    t = KINDS[kind]["table"]
    cursor.execute(
        f"SELECT file_path FROM {t}_episode_image "
        "WHERE episode_code = %s AND file_path IS NOT NULL ORDER BY page_no",
        (episode_code,),
    )
    paths = [Path(r["file_path"]) for r in cursor.fetchall()]
    paths = [p for p in paths if p.is_file() and 0 < p.stat().st_size <= MAX_IMAGE_BYTES]
    if len(paths) > MAX_IMAGES:
        step = len(paths) / MAX_IMAGES
        paths = [paths[int(i * step)] for i in range(MAX_IMAGES)]

    blocks = []
    for p in paths:
        data = p.read_bytes()
        media_type = image_media_type(data)
        if not media_type:
            continue
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type,
                       "data": base64.standard_b64encode(data).decode("ascii")},
        })
    return blocks


def episode_heading(episode: dict) -> str:
    no, title = episode.get("episode_no"), (episode.get("episode_title") or "").strip()
    if no is None:
        return title or "회차"
    return f"{no}화 {title}" if title and title != f"{no}화" else f"{no}화"


def analyze_episode(client, cfg: dict, kind: str, work_title: str, episode: dict,
                    images: list[dict], feedback: str = "") -> dict:
    """회차 내용을 Claude 로 분석해 {"summary", "comment", "model"} 를 받습니다."""
    text = f"작품: {work_title}\n회차: {episode_heading(episode)}"
    if kind == "novel":
        system = SYSTEM_PROMPTS["novel"]
        text += f"\n\n<본문>\n{episode['content']}\n</본문>"
    else:
        system = SYSTEM_PROMPTS["image"].format(label=KINDS[kind]["label"])
        text += f"\n\n위 이미지 {len(images)}장은 이 회차에서 순서대로 고르게 뽑은 장면입니다."
    if feedback:
        text += f"\n\n이전에 쓴 댓글이 규정 검사에서 거절됐습니다: {feedback}\n이 점을 고쳐 다시 쓰세요."

    response = client.beta.messages.create(
        model=cfg["model"],
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        output_config={
            "effort": cfg["effort"],
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
        messages=[{"role": "user", "content": [*images, {"type": "text", "text": text}]}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("모델이 응답을 거절했습니다.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("응답이 max_tokens 에서 잘렸습니다.")
    data = json.loads(next((b.text for b in response.content if b.type == "text"), ""))
    return {
        "summary": data["summary"].strip(),
        "comment": data["comment"].strip(),
        "model": response.model,
    }


def recommend_for_work(cursor, client, cfg: dict, kind: str, work: dict) -> tuple[bool, str]:
    """작품 1개 처리 — (저장 여부, 결과 메시지)를 반환합니다."""
    episode = pick_episode(cursor, kind, work["work_id"])
    images: list[dict] = []
    if kind == "novel":
        if not episode or not (episode["content"] or "").strip():
            return False, "본문 있는 회차 없음 → 건너뜀"
    else:
        if episode:
            images = episode_images(cursor, kind, episode["episode_code"])
        if not images:
            return False, "읽을 수 있는 회차 이미지 없음 → 건너뜀"

    feedback = ""
    for _ in range(MAX_ATTEMPTS):
        result = analyze_episode(client, cfg, kind, work["title"], episode, images, feedback)
        errors, _warnings = rules.check(result["comment"])
        if not errors:
            break
        feedback = " / ".join(errors)
    else:
        return False, f"규정 통과 실패 → 저장 안 함 ({feedback})"

    source_url = (f"{episode_origin(work['source_url'])}/{KINDS[kind]['segment']}"
                  f"/{work['work_id']}/{episode['episode_code']}")
    cursor.execute(
        """
        INSERT IGNORE INTO book_comment_recommend
            (kind, work_id, episode_code, episode_no, work_title, episode_title,
             source_url, summary, body, model)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            kind, work["work_id"], episode["episode_code"], episode["episode_no"], work["title"],
            episode["episode_title"], source_url, result["summary"], result["comment"], result["model"],
        ),
    )
    if cursor.rowcount == 0:
        return False, f"{episode_heading(episode)} 추천이 이미 있음 → 건너뜀"
    return True, f"{episode_heading(episode)} 저장: {result['comment']}"


def run(conn, kind: str, count: int) -> int:
    """추천 댓글을 만들고 저장한 건수를 반환합니다. 작품마다 바로 commit 해 화면에서 진행이 보입니다."""
    if kind not in KINDS:
        raise SystemExit(f"알 수 없는 구분: {kind} (novel / toon / manhwa)")

    cfg = load_anthropic_config()
    client = make_client(cfg)
    saved = 0
    with conn.cursor(pymysql.cursors.DictCursor) as cursor:
        works = pick_works(cursor, kind, count)
        print(f"[{KINDS[kind]['label']}] 대상 작품 {len(works)}개 (요청 {count}개, 모델 {cfg['model']})", flush=True)
        for i, work in enumerate(works, 1):
            try:
                ok, message = recommend_for_work(cursor, client, cfg, kind, work)
                saved += ok
            except Exception as err:  # 작품 하나 실패해도 나머지는 계속
                message = f"오류: {type(err).__name__}: {err}"
            conn.commit()
            print(f"[{i}/{len(works)}] {work['work_id']} {work['title']} — {message}", flush=True)
    print(f"완료: {saved}건 저장", flush=True)
    return saved
