-- CommentFlow 테이블 생성 (db.py 가 접속 시 자동 실행)
-- 즐겨찾기 작품은 book_web_Crawler 의 book_work_list (used = 'Y') 를 그대로 사용합니다.
-- book_work_list 는 크롤러가 즐겨찾기 동기화 시 행을 삭제하므로 FK 를 걸지 않습니다.

CREATE TABLE IF NOT EXISTS book_comment (
    comment_seq     BIGINT          NOT NULL AUTO_INCREMENT COMMENT '내부 PK',
    book_id         BIGINT          NOT NULL COMMENT '소설 아이디 (book_work_list.book_id)',
    episode_url     VARCHAR(500)    NOT NULL COMMENT '댓글 달 회차 URL',
    memo            TEXT            NOT NULL COMMENT '직접 읽고 적은 감상 메모',
    body            TEXT            NOT NULL COMMENT '최종 댓글 내용',
    status          CHAR(1)         NOT NULL DEFAULT 'D' COMMENT '상태 (D:초안, C:복사함, P:등록완료)',
    posted_at       DATETIME        NULL COMMENT '등록 완료 일시',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (comment_seq),
    KEY idx_book_id (book_id),
    KEY idx_status (status),
    CONSTRAINT chk_comment_status CHECK (status IN ('D','C','P'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='즐겨찾기 작품 댓글 초안 / 등록 기록';
