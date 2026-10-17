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
