import configparser
from contextlib import contextmanager
from pathlib import Path

import pymysql

CONFIG_NAME = "config.ini"
SCHEMA_SQL = Path(__file__).resolve().parent / "comment_sql" / "00_comment_테이블생성.sql"

_schema_checked = False


def config_path() -> Path:
    return Path(__file__).resolve().parent / CONFIG_NAME


def load_mysql_config(path: Path | None = None) -> dict:
    config_file = path or config_path()
    parser = configparser.ConfigParser(interpolation=None)
    read_files = parser.read(config_file, encoding="utf-8")
    if not read_files:
        raise FileNotFoundError(f"DB 설정 파일을 찾을 수 없습니다: {config_file}")
    if not parser.has_section("mysql"):
        raise ValueError(f"[mysql] 섹션이 없습니다: {config_file}")

    section = parser["mysql"]
    return {
        "host": section.get("host", "").strip(),
        "port": section.getint("port", 3306),
        "user": section.get("user", "").strip(),
        "password": section.get("password", ""),
        "database": section.get("database", "").strip(),
        "charset": section.get("charset", "utf8mb4").strip() or "utf8mb4",
    }


def schema_statements(path: Path = SCHEMA_SQL) -> list[str]:
    """comment_sql/00_comment_테이블생성.sql 을 '--' 주석 제거 후 ';' 기준 구문 목록으로 반환합니다."""
    lines = [
        line
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if not line.strip().startswith("--")
    ]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def ensure_schema(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SET NAMES utf8mb4")
        for stmt in schema_statements():
            cursor.execute(stmt)


@contextmanager
def get_connection():
    global _schema_checked
    settings = load_mysql_config()
    conn = pymysql.connect(
        host=settings["host"],
        port=settings["port"],
        user=settings["user"],
        password=settings["password"],
        database=settings["database"],
        charset=settings["charset"],
        autocommit=False,
        connect_timeout=15,
        init_command="SET NAMES utf8mb4",
    )
    try:
        if not _schema_checked:
            ensure_schema(conn)
            _schema_checked = True
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
