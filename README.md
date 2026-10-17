# CommentFlow

커뮤니티 관리

## 폴더

- `00_project_doc` : 프로젝트 문서
- `01_Claude_job` : Claude 작업 기록
- `02_cursor_job` : Cursor 작업 기록
- `comment_sql` : 테이블 생성 / 조회 / 관리 SQL
- `Dev_work` : pull·push 스크립트, 실행 명령 메모

## 댓글 초안 도우미 (`commentflow.py`)

직접 읽은 회차의 감상 메모로 댓글 초안을 만들고, 사이트 댓글 규정(`rules.py`)을 검사한 뒤
클립보드 복사 + 회차 페이지 열기까지 해 줍니다. **등록 버튼은 직접 누릅니다.**

```
pip install -r requirements.txt

python commentflow.py works        # 즐겨찾기 작품 (book_work_list, used='Y')
python commentflow.py draft https://toki35.com/novel/59094/6063116 --memo "제목이 세계재벌이라 부를 쌓는 이야기를 기대했는데 전쟁(분쟁) 파트가 너무 많아서 약간 아쉬움"
python commentflow.py copy 1        # 클립보드 복사 + 페이지 열기 → 직접 붙여넣고 등록 → y
python commentflow.py list --status P   # D:초안, C:복사함, P:등록완료
python commentflow.py check "잘봤어요"   # 규정 검사만
```

- DB 는 `book_web_Crawler` 와 같은 MySQL(`onebabo_book`)을 씁니다. 즐겨찾기 작품은
  크롤러가 관리하는 `book_work_list`(used='Y') 기준이며, 여기에 있는 작품에만 초안을 만들 수 있습니다.
- 댓글 기록은 `book_comment` 테이블에 저장됩니다 (`comment_sql/00_comment_테이블생성.sql`, 접속 시 자동 생성).
- 인사말뿐인 댓글, 유료 회차/절차 불평 단어가 들어간 댓글은 저장되지 않습니다.

DB 접속은 `config.example.ini`를 `config.ini`로 복사해 채웁니다. `config.ini`는 git에 올리지 않습니다.

## 추천 댓글 생성 (`recommend`, book_web `/comment_list`)

book_web `/comment_list` 의 **[댓글 생성]** 버튼이 아래 명령을 실행합니다. 터미널에서 직접 실행해도 됩니다.

```
python commentflow.py recommend --kind novel --count 20   # novel:소설, toon:웹툰, manhwa:만화
```

1. 추천 댓글이 아직 없는 보유 작품을 랜덤으로 `--count` 개 고릅니다
   (소설: `book_work_list` used='Y', 웹툰·만화: `{toon,manhwa}_info` 중 수집 보류 제외).
2. 작품마다 최근 읽은 회차(없으면 수집된 최신 회차)를 Gemini 로 분석합니다
   (소설: 본문 텍스트, 웹툰·만화: 회차 이미지 최대 8장).
3. `rules.py` 규정 검사를 통과한 댓글만 `book_comment_recommend` 테이블에 저장합니다
   (`comment_sql/01_comment_recommend_테이블생성.sql`, 접속 시 자동 생성).

작품을 지정하면 그 작품에서만, 추천이 아직 없는 회차를 최근 읽은 순으로 `--count` 개 만듭니다
(`/comment_list` 의 ID 입력폼으로 조회한 상태에서 [댓글 생성]을 누르면 이 방식으로 실행).

```
python commentflow.py recommend --kind novel --work-id 59094 --count 10
```

**사이트에 댓글을 등록하지 않습니다.** `/comment_list` 에서 댓글을 고쳐 저장하고 **[연동]** 을 누르면
댓글이 클립보드에 복사되고 원글 회차 페이지가 열립니다. 붙여넣고 등록 버튼은 직접 누른 뒤 **[등록완료]** 로 표시합니다.

Gemini API 설정은 `config.ini` 의 `[gemini]` 섹션입니다 (`pip install -r requirements.txt` 로 `google-genai` 설치).
키는 [Google AI Studio](https://aistudio.google.com/apikey) 에서 무료 발급합니다.
`api_key` 를 비우면 `GEMINI_API_KEY` 환경변수를 씁니다. 기본 모델은 `gemini-2.5-flash` 입니다.
