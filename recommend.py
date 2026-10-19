"""회차 내용 분석 → 추천 댓글 생성 (book_web /comment_list 의 [댓글 생성] 버튼이 호출).

1) 추천 댓글이 아직 없는 보유 작품을 랜덤으로 N개 고르고
   - 소설: book_work_list (used='Y'), 본문이 수집된 회차가 있는 작품
   - 웹툰/만화: {toon,manhwa}_info, 이미지가 수집된 회차가 있고 수집 보류가 아닌 작품
2) 작품마다 읽은 지 가장 오래된 회차(없으면 수집된 최신 회차)를 Gemini 로 분석해
   - 소설: 본문 텍스트 (book_info_chapter.content)
   - 웹툰/만화: 회차 이미지 일부 ({kind}_episode_image.file_path, 최대 MAX_IMAGES 장)
3) 사이트 댓글 규정(rules.py)을 통과한 감상 댓글만 book_comment_recommend 에 저장합니다.

work_id(--book-id)를 주면 그 작품만 대상으로, 추천 댓글이 아직 없는 회차를
읽은 지 오래된 순으로 세워 READ_STEP 칸 간격으로(모자라면 남은 회차, 읽은 기록이 없으면 최신 회차 순)
최대 N개 골라 회차마다 추천 댓글을 만듭니다.

사이트에 댓글을 등록하지는 않습니다. 등록은 /comment_list 에서 [연동] 후 사용자가 직접 합니다.
"""
import configparser
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pymysql.cursors

import rules
from db import config_path

DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_ORIGIN = "https://toki35.com"
PENDING_TITLE_PREFIX = "[등록대기]"
MAX_ATTEMPTS = 2
MAX_COMMENT_CHARS = 50  # 추천 댓글 길이 상한 (공백 포함)
READ_STEP = 3  # 읽은 회차는 오래된 순으로 이 간격마다 1개씩 선정 (1,3,5,7,8,10,12 → 1,7,12)
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 4_500_000  # API 이미지 1장 한도(5MB) 아래로
MAX_NOVEL_CHARS = 100_000  # 무료 티어·요청 크기 여유

# 무료 한도(429)·모델 불가(404) 시 이 순서로 전환 (config [gemini] models 로 덮어쓸 수 있음)
MODEL_PRIORITY = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]

# kind → 화면 표기 · 사이트 URL 경로 · 테이블 접두어 · 작품 ID 컬럼
KINDS = {
    "novel": {"label": "웹소설", "segment": "novel"},
    "toon": {"label": "웹툰", "segment": "webtoon", "table": "toon", "id": "toon_id"},
    "manhwa": {"label": "만화", "segment": "manhwa", "table": "manhwa", "id": "manhwa_id"},
}

RULES_PROMPT = f"""반드시 지킬 사이트 댓글 규정:
- 작품 감상평이 아닌 의미 없는 댓글("감사합니다", "잘봤어요", "수고하셨습니다" 등)은 금지입니다. 인사말만으로 끝내지 말고, 이번 회차의 구체적인 장면·인물·전개에 대한 감상을 담으세요.
- 유료 회차, 결제, 포인트, 크롤링, 아류 사이트, 인증·잠금 절차 등 사이트 운영에 대한 언급이나 불평은 절대 쓰지 마세요. 내용에 같은 단어가 나와도 댓글에는 쓰지 마세요.

댓글 작성 기준:
- 1~2문장, 공백 포함 {MAX_COMMENT_CHARS}자 이내 (넘으면 거절됩니다). 실제 독자가 쓴 것처럼 자연스러운 존댓말 또는 가벼운 구어체.
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
        "comment": {"type": "string", "description": f"규정을 지킨 감상 댓글 ({MAX_COMMENT_CHARS}자 이내)"},
    },
    "required": ["summary", "comment"],
}


def _parse_model_list(raw: str) -> list[str]:
    """쉼표·세미콜론·줄바꿈으로 구분된 모델 ID 목록."""
    parts: list[str] = []
    for chunk in (raw or "").replace(";", ",").replace("\n", ",").split(","):
        name = chunk.strip()
        if name and name not in parts:
            parts.append(name)
    return parts


def load_gemini_config() -> dict:
    """config.ini [gemini] (api_key, model, models). api_key 가 없으면 GEMINI_API_KEY 환경변수.

    model 은 시작 모델, models 는 한도 소진 시 전환 우선순위(미지정이면 MODEL_PRIORITY).
    """
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config_path(), encoding="utf-8")
    section = parser["gemini"] if parser.has_section("gemini") else {}
    preferred = (section.get("model", "") or "").strip() or DEFAULT_MODEL
    models = _parse_model_list(section.get("models", "") or "") or list(MODEL_PRIORITY)
    if preferred in models:
        models = [preferred] + [m for m in models if m != preferred]
    else:
        models = [preferred] + models
    return {
        "api_key": (section.get("api_key", "") or "").strip() or None,
        "model": models[0],
        "models": models,
    }


def make_client(cfg: dict):
    from google import genai

    api_key = cfg["api_key"] or (os.environ.get("GEMINI_API_KEY") or "").strip() or None
    if not api_key:
        raise SystemExit(
            "Gemini API 키가 없습니다. https://aistudio.google.com/apikey 에서 무료 키를 발급해 "
            "config.ini [gemini] api_key 에 넣거나 GEMINI_API_KEY 환경변수를 설정하세요."
        )
    return genai.Client(api_key=api_key)


def _error_status_code(err: BaseException) -> int | None:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if isinstance(code, int):
        return code
    # google.genai.errors.ClientError: details 또는 message 에 code 가 들어 있음
    details = getattr(err, "details", None)
    if isinstance(details, dict):
        nested = details.get("error") if isinstance(details.get("error"), dict) else details
        if isinstance(nested, dict) and isinstance(nested.get("code"), int):
            return nested["code"]
    return None


def is_quota_exhausted(err: BaseException) -> bool:
    """무료/유료 할당량·분당 한도 소진(429 RESOURCE_EXHAUSTED)."""
    code = _error_status_code(err)
    text = str(err).upper()
    return (
        code == 429
        or "RESOURCE_EXHAUSTED" in text
        or ("QUOTA" in text and "EXCEED" in text)
    )


def is_model_unavailable(err: BaseException) -> bool:
    """모델 없음·종료(404 NOT_FOUND / no longer available)."""
    code = _error_status_code(err)
    text = str(err).lower()
    return code == 404 or "not_found" in text or "no longer available" in text


class ModelSession:
    """실행 중 현재 모델 추적. 한도·404 시 다음 우선순위 모델로 전환."""

    def __init__(self, models: list[str]):
        if not models:
            raise ValueError("모델 목록이 비어 있습니다.")
        self.models = list(models)
        self.index = 0

    @property
    def current(self) -> str:
        return self.models[self.index]

    def advance(self, reason: str) -> str | None:
        """현재 모델을 포기하고 다음으로. 더 없으면 None."""
        old = self.current
        if self.index + 1 >= len(self.models):
            print(f"[모델] {old} - {reason}. 남은 후보 없음.", flush=True)
            return None
        self.index += 1
        nxt = self.current
        print(f"[모델] {old} - {reason} -> {nxt} 로 전환", flush=True)
        return nxt


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


def pick_work(cursor, kind: str, work_id: int) -> dict | None:
    """지정한 작품 1개 → {work_id, title, source_url} (추천·초안 기록·수집 보류 여부는 따지지 않음)"""
    if kind == "novel":
        cursor.execute(
            """
            SELECT w.book_id AS work_id, COALESCE(i.title, w.title) AS title, w.source_url
              FROM book_work_list AS w
              LEFT JOIN book_info AS i ON i.book_id = w.book_id
             WHERE w.book_id = %s
            """,
            (work_id,),
        )
        return cursor.fetchone()

    t, idc = KINDS[kind]["table"], KINDS[kind]["id"]
    cursor.execute(
        f"""
        SELECT t.{idc} AS work_id, t.title, COALESCE(w.source_url, t.source_url) AS source_url
          FROM {t}_info AS t
          LEFT JOIN {t}_work_list AS w ON w.{idc} = t.{idc}
         WHERE t.{idc} = %s
        """,
        (work_id,),
    )
    return cursor.fetchone()


def pick_episodes(cursor, kind: str, work_id: int, limit: int = 1) -> list[dict]:
    """추천 댓글이 없는 회차 limit 개 → [{episode_code, episode_no, episode_title, content?}]

    읽은 회차를 읽은 지 오래된 순(읽은 시각이 같으면 앞 회차부터)으로 세워 READ_STEP 칸마다 1개씩 고르고,
    모자라면 남은 후보(건너뛴 읽은 회차 → 읽지 않은 회차 최신 순)로 채웁니다."""
    candidates = episode_candidates(cursor, kind, work_id)
    read = [e for e in candidates if e["is_read"]]
    picked = read[::READ_STEP][:limit]
    codes = {e["episode_code"] for e in picked}
    picked += [e for e in candidates if e["episode_code"] not in codes][:limit - len(picked)]

    if kind == "novel" and picked:
        cursor.execute(
            "SELECT episode_code, content FROM book_info_chapter WHERE book_id = %s AND episode_code IN %s",
            (work_id, [e["episode_code"] for e in picked]),
        )
        contents = {r["episode_code"]: r["content"] for r in cursor.fetchall()}
        for e in picked:
            e["content"] = contents.get(e["episode_code"])
    return picked


def episode_candidates(cursor, kind: str, work_id: int) -> list[dict]:
    """추천 댓글이 없는 회차 전체를 읽은 회차(오래된 순) → 읽지 않은 회차(최신 순)로 정렬
    → [{episode_code, episode_no, episode_title, is_read}]"""
    if kind == "novel":
        cursor.execute(
            """
            SELECT c.episode_code, c.episode_no, c.episode_title, (c.read_yn = 'Y') AS is_read
              FROM book_info_chapter AS c
             WHERE c.book_id = %s AND c.has_content = 1
               AND NOT EXISTS (SELECT 1 FROM book_comment_recommend AS r
                                WHERE r.kind = 'novel' AND r.work_id = c.book_id
                                  AND r.episode_code = c.episode_code)
             ORDER BY (c.read_yn = 'Y') DESC,
                      CASE WHEN c.read_yn = 'Y' THEN c.read_at END,
                      CASE WHEN c.read_yn = 'Y' THEN c.episode_no END,
                      c.episode_no DESC
            """,
            (work_id,),
        )
        return cursor.fetchall()

    t, idc = KINDS[kind]["table"], KINDS[kind]["id"]
    cursor.execute(
        f"""
        SELECT e.episode_code, e.episode_no, e.episode_title, (r.{idc} IS NOT NULL) AS is_read
          FROM {t}_episode AS e
          LEFT JOIN {t}_episode_read AS r ON r.{idc} = e.{idc} AND r.episode_no = e.episode_no
         WHERE e.{idc} = %s AND e.has_images = 1
           AND NOT EXISTS (SELECT 1 FROM book_comment_recommend AS cr
                            WHERE cr.kind = %s AND cr.work_id = e.{idc}
                              AND cr.episode_code = e.episode_code)
         ORDER BY (r.{idc} IS NOT NULL) DESC, r.read_at,
                  CASE WHEN r.{idc} IS NOT NULL THEN e.episode_no END,
                  e.episode_no DESC
        """,
        (work_id, kind),
    )
    return cursor.fetchall()


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
    """회차 이미지 중 최대 MAX_IMAGES 장을 고르게 골라 {mime_type, data} 목록으로 만듭니다."""
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
        blocks.append({"mime_type": media_type, "data": data})
    return blocks


def episode_heading(episode: dict) -> str:
    no, title = episode.get("episode_no"), (episode.get("episode_title") or "").strip()
    if no is None:
        return title or "회차"
    return f"{no}화 {title}" if title and title != f"{no}화" else f"{no}화"


def analyze_episode(client, models: ModelSession, kind: str, work_title: str, episode: dict,
                    images: list[dict], feedback: str = "") -> dict:
    """회차 내용을 Gemini 로 분석해 {"summary", "comment", "model"} 를 받습니다.

    무료 한도(429)·모델 불가(404) 이면 models 우선순위로 바꿔 같은 회차를 재시도합니다.
    """
    from google.genai import types

    text = f"작품: {work_title}\n회차: {episode_heading(episode)}"
    if kind == "novel":
        system = SYSTEM_PROMPTS["novel"]
        body = (episode["content"] or "").strip()
        if len(body) > MAX_NOVEL_CHARS:
            body = body[:MAX_NOVEL_CHARS] + "\n…(본문 일부 생략)"
        text += f"\n\n<본문>\n{body}\n</본문>"
    else:
        system = SYSTEM_PROMPTS["image"].format(label=KINDS[kind]["label"])
        text += f"\n\n위 이미지 {len(images)}장은 이 회차에서 순서대로 고르게 뽑은 장면입니다."
    if feedback:
        text += f"\n\n이전에 쓴 댓글이 규정 검사에서 거절됐습니다: {feedback}\n이 점을 고쳐 다시 쓰세요."

    parts: list = [
        types.Part.from_bytes(data=img["data"], mime_type=img["mime_type"]) for img in images
    ]
    parts.append(text)

    while True:
        model = models.current
        try:
            response = client.models.generate_content(
                model=model,
                contents=parts,
                config={
                    "system_instruction": system,
                    "response_mime_type": "application/json",
                    "response_json_schema": OUTPUT_SCHEMA,
                    "temperature": 0.8,
                },
            )
        except Exception as err:
            if is_quota_exhausted(err):
                if models.advance("무료/할당량 한도(429)") is None:
                    raise
                continue
            if is_model_unavailable(err):
                if models.advance("모델 사용 불가(404)") is None:
                    raise
                continue
            raise

        raw = (response.text or "").strip()
        if not raw:
            raise RuntimeError("모델 응답이 비어 있습니다.")
        data = json.loads(raw)
        model_name = getattr(response, "model_version", None) or model
        return {
            "summary": data["summary"].strip(),
            "comment": data["comment"].strip(),
            "model": model_name,
        }


def recommend_for_episode(cursor, client, models: ModelSession, kind: str, work: dict,
                          episode: dict | None) -> tuple[bool, str]:
    """회차 1개 처리 — (저장 여부, 결과 메시지)를 반환합니다."""
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
        result = analyze_episode(client, models, kind, work["title"], episode, images, feedback)
        errors, _warnings = rules.check(result["comment"])
        if len(result["comment"]) > MAX_COMMENT_CHARS:
            errors.append(f"댓글이 {len(result['comment'])}자입니다. {MAX_COMMENT_CHARS}자 이내로 줄이세요.")
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


def run(conn, kind: str, count: int, work_id: int | None = None) -> int:
    """추천 댓글을 만들고 저장한 건수를 반환합니다. 건마다 바로 commit 해 화면에서 진행이 보입니다.

    work_id 가 없으면 추천 댓글이 없는 작품 count 개(작품당 1회차),
    있으면 그 작품에서 추천 댓글이 없는 회차 count 개를 대상으로 합니다."""
    if kind not in KINDS:
        raise SystemExit(f"알 수 없는 구분: {kind} (novel / toon / manhwa)")

    cfg = load_gemini_config()
    client = make_client(cfg)
    models = ModelSession(cfg["models"])
    label = KINDS[kind]["label"]
    saved = 0
    with conn.cursor(pymysql.cursors.DictCursor) as cursor:
        if work_id:
            work = pick_work(cursor, kind, work_id)
            if not work:
                print(f"[{label}] 작품 ID {work_id} 를 찾을 수 없습니다.", flush=True)
                print("완료: 0건 저장", flush=True)
                return 0
            jobs = [(work, ep) for ep in pick_episodes(cursor, kind, work_id, count)]
            print(f"[{label}] {work_id} {work['title']} — 추천 없는 회차 {len(jobs)}개 "
                  f"(요청 {count}개, 시작 모델 {models.current})", flush=True)
        else:
            works = pick_works(cursor, kind, count)
            jobs = [(w, next(iter(pick_episodes(cursor, kind, w["work_id"])), None)) for w in works]
            print(f"[{label}] 대상 작품 {len(jobs)}개 (요청 {count}개, 시작 모델 {models.current})", flush=True)
        print(f"[모델] 전환 우선순위: {' -> '.join(cfg['models'])}", flush=True)
        for i, (work, episode) in enumerate(jobs, 1):
            try:
                ok, message = recommend_for_episode(cursor, client, models, kind, work, episode)
                saved += ok
            except Exception as err:  # 하나 실패해도 나머지는 계속
                message = f"오류: {type(err).__name__}: {err}"
            conn.commit()
            print(f"[{i}/{len(jobs)}] {work['work_id']} {work['title']} — {message}", flush=True)
    print(f"완료: {saved}건 저장 (마지막 모델 {models.current})", flush=True)
    return saved
