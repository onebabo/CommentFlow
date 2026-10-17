"""사이트 댓글 규정 검사.

- 작품 감상평이 아닌 의미 없는 댓글("감사합니다", "잘봤어요" 등) 금지
- 유료 회차 / 절차 관련 불평 금지
"""
import re

MIN_MEANINGFUL_CHARS = 15

FILLER_PHRASES = [
    "감사합니다", "감사해요", "고맙습니다",
    "잘봤어요", "잘 봤어요", "잘봤습니다", "잘 봤습니다",
    "잘보고 갑니다", "잘 보고 갑니다", "잘보고갑니다",
    "수고하셨습니다", "수고하세요",
    "재밌어요", "재밌네요", "재미있어요", "재미있네요",
]

COMPLAINT_KEYWORDS = [
    "유료", "결제", "포인트", "크롤링", "아류", "인증", "잠금", "절차", "왜 막", "못 보게",
]

_NOISE = re.compile(r"[\s\W_ㅋㅎㅠㅜ]+")


def check(text: str) -> tuple[list[str], list[str]]:
    """(errors, warnings) 를 반환합니다. errors 가 있으면 등록하면 안 됩니다."""
    errors: list[str] = []
    warnings: list[str] = []
    body = text.strip()
    if not body:
        return ["댓글 내용이 비어 있습니다."], warnings

    found = [k for k in COMPLAINT_KEYWORDS if k in body]
    if found:
        errors.append(f"유료 회차/절차 불평으로 보일 수 있는 단어: {', '.join(found)}")

    core = body
    for phrase in FILLER_PHRASES:
        core = core.replace(phrase, "")
    core = _NOISE.sub("", core)
    if len(core) < MIN_MEANINGFUL_CHARS:
        errors.append(
            f"감상 내용이 너무 짧습니다 (인사말 제외 {len(core)}자, 최소 {MIN_MEANINGFUL_CHARS}자)."
        )

    if len(body) > 500:
        warnings.append("댓글이 500자를 넘습니다. 너무 길지 않은지 확인하세요.")
    return errors, warnings
