-- CommentFlow 추천 댓글 테이블 (db.py 가 접속 시 자동 실행)
-- `python commentflow.py recommend` 가 회차 내용을 분석해 만든 추천 댓글을 저장하고,
-- book_web /comment_list 화면에서 수정·저장·연동(복사 + 원글 회차 열기)·등록완료 처리한다.
-- 사이트 등록 버튼은 항상 사용자가 직접 누른다.
-- 웹툰·만화 회차 코드는 'nv-847903-1' 같은 문자열이라 episode_code 는 VARCHAR 로 둔다.

CREATE TABLE IF NOT EXISTS book_comment_recommend (
    rec_seq         BIGINT          NOT NULL AUTO_INCREMENT COMMENT '내부 PK',
    kind            VARCHAR(10)     NOT NULL DEFAULT 'novel' COMMENT '구분 (novel:소설, toon:웹툰, manhwa:만화)',
    work_id         BIGINT          NOT NULL COMMENT '작품 아이디 (book_id / toon_id / manhwa_id)',
    episode_code    VARCHAR(100)    NOT NULL COMMENT '회차 코드 (book_info_chapter / {toon,manhwa}_episode.episode_code)',
    episode_no      INT             NULL COMMENT '회차 번호',
    work_title      VARCHAR(300)    NOT NULL COMMENT '작품 제목 (생성 시점)',
    episode_title   VARCHAR(300)    NULL COMMENT '회차 제목',
    source_url      VARCHAR(500)    NOT NULL COMMENT '원글 회차 URL (생성 시점 도메인)',
    summary         TEXT            NULL COMMENT '회차 내용 요약 (분석 결과)',
    body            TEXT            NOT NULL COMMENT '추천 댓글 내용 (수정 가능)',
    status          CHAR(1)         NOT NULL DEFAULT 'R' COMMENT '상태 (R:추천, S:수정저장, C:연동함, P:등록완료)',
    model           VARCHAR(60)     NULL COMMENT '생성 모델',
    linked_at       DATETIME        NULL COMMENT '마지막 연동(복사+원글 열기) 일시',
    posted_at       DATETIME        NULL COMMENT '등록 완료 일시',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (rec_seq),
    UNIQUE KEY uq_episode (kind, work_id, episode_code),
    KEY idx_kind_seq (kind, rec_seq),
    KEY idx_work (kind, work_id),
    CONSTRAINT chk_recommend_status CHECK (status IN ('R','S','C','P'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='회차 분석 추천 댓글 (book_web /comment_list)';
