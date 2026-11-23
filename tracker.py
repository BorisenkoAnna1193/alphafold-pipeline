import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# run_id = datetime.now(timezone.utc).isoformat(timespec="seconds")


class Tracker:
    def __init__(self, db_path, run_id=None):
        if not run_id:
            run_id = datetime.now(UTC).isoformat(timespec="seconds")
        self.db_path = Path(db_path)
        self.run_id = run_id
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self._create_table()

    def record(
        self,
        entry_id,
        file_type,
        status,
        filename=None,
        url=None,
        http_status=None,
        error_message=None,
        size_bytes=None,
    ):
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        with self.conn:
            self.cursor.execute(
                """
            INSERT INTO downloads_history (run_id, entry_id, file_type, timestamp, status, url, http_status, error_message, size_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
                (
                    self.run_id,
                    entry_id,
                    file_type,
                    timestamp,
                    status,
                    url,
                    http_status,
                    error_message,
                    size_bytes,
                ),
            )
            self.cursor.execute(
                """
        INSERT INTO downloads_current_state (entry_id, file_type, url, filename, status, http_status,
                                     error_message, attempts, last_attempt_time, size_bytes)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT (entry_id, file_type) DO UPDATE SET
             url = excluded.url, filename = excluded.filename,
             status = excluded.status, http_status = excluded.http_status,
             error_message = excluded.error_message,
                last_attempt_time = excluded.last_attempt_time,
                size_bytes = excluded.size_bytes,
                attempts = downloads_current_state.attempts + 1;
        """,
                (
                    entry_id,
                    file_type,
                    url,
                    filename,
                    status,
                    http_status,
                    error_message,
                    timestamp,
                    size_bytes,
                ),
            )

    #  self.conn.commit()

    def _create_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS downloads_current_state (
                entry_id TEXT,
                file_type TEXT,
                url TEXT,
                filename TEXT,
                status TEXT,
                http_status INTEGER,
                error_message TEXT,
                attempts INTEGER,
                last_attempt_time TEXT,
                size_bytes INTEGER,
                PRIMARY KEY (entry_id, file_type) 
            )
                """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS downloads_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                timestamp TEXT,
                entry_id TEXT,
                file_type TEXT,
                url TEXT,
                status TEXT,
                http_status INTEGER,
                error_message TEXT,
                size_bytes INTEGER
            )
                """)
        self.cursor.execute("""
           CREATE INDEX IF NOT EXISTS idx_history_run_id ON downloads_history (run_id);
        """)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def get_records_history(self):
        self.cursor.execute("SELECT * FROM downloads_history")
        return self.cursor.fetchall()

    def get_records_current_state(self):
        self.cursor.execute("SELECT * FROM downloads_current_state")
        return self.cursor.fetchall()

    def get(self, entry_id, file_type):
        self.cursor.execute(
            """
        SELECT * FROM downloads_current_state WHERE entry_id = ? AND file_type = ?;
        """,
            (entry_id, file_type),
        )
        return self.cursor.fetchone()  # fetchall() всегда возвращает список (может пустой) и никогда не возвращает None

    def should_download(self, entry_id, file_type, file_path):
        row = self.get(entry_id, file_type)
        if row is None:  # ни разу не пробовали
            return True  # No record exists, should download
        if row["status"] == "not_found":  # 404 — повтор не поможет
            return False
        if row["status"] == "ok":
            file_path = Path(file_path)
            if file_path.exists() and file_path.stat().st_size > 0:
                return False  # уже есть, качать не нужно

        return True  # Download if the last status was not 'success'
