"""ProjectState — fazlar arasi devredilen kalici proje hafizasi.

RAG deposuyla ayni SQLite dosyasini paylasir (farkli tablolar). Her varlik
`key` alaniyla tekil: ajan ayni anahtari tekrar kaydederse ustune yazilir, bu da
tekrar calistirmalari (idempotent yeniden kosu) guvenli kilar.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..i18n import t
from ..logging import get_logger
from .models import (
    Artifact,
    Decision,
    Gap,
    Phase,
    PhaseState,
    Question,
    Requirement,
    ResearchNote,
    Status,
    Task,
)

log = get_logger("state")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phase_state (
    phase       TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'pending',
    summary     TEXT NOT NULL DEFAULT '',
    -- Fazin tamamlandigi andaki proje hedefi. Bos ise kokeni bilinmiyor
    -- demektir (bu sutundan onceki kayitlar) ve tamamlanma guvenilmez.
    goal        TEXT NOT NULL DEFAULT '',
    started_at  REAL,
    finished_at REAL,
    cost_usd    REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS requirements (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'functional',
    priority    TEXT NOT NULL DEFAULT 'should',
    source_ref  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS gaps (
    id             INTEGER PRIMARY KEY,
    key            TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    severity       TEXT NOT NULL DEFAULT 'medium',
    area           TEXT NOT NULL DEFAULT 'genel',
    recommendation TEXT NOT NULL DEFAULT '',
    evidence       TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    choice       TEXT NOT NULL DEFAULT '',
    rationale    TEXT NOT NULL DEFAULT '',
    alternatives TEXT NOT NULL DEFAULT '',
    tradeoffs    TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS research_notes (
    id         INTEGER PRIMARY KEY,
    topic      TEXT NOT NULL,
    finding    TEXT NOT NULL,
    url        TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'medium',
    created_at REAL NOT NULL,
    UNIQUE(topic, finding)
);

CREATE TABLE IF NOT EXISTS questions (
    id         INTEGER PRIMARY KEY,
    key        TEXT NOT NULL UNIQUE,
    question   TEXT NOT NULL,
    why        TEXT NOT NULL DEFAULT '',
    asked_by   TEXT NOT NULL DEFAULT 'analyst',
    blocking   INTEGER NOT NULL DEFAULT 1,
    suggestion TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'open',
    answer     TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    answered_at REAL
);

CREATE TABLE IF NOT EXISTS plans (
    id          TEXT PRIMARY KEY,
    seq         INTEGER NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'code',
    lane        TEXT NOT NULL DEFAULT 'backend',
    deps        TEXT NOT NULL DEFAULT '[]',
    files       TEXT NOT NULL DEFAULT '[]',
    acceptance  TEXT NOT NULL DEFAULT '',
    estimate    TEXT NOT NULL DEFAULT 'M',
    status      TEXT NOT NULL DEFAULT 'pending',
    result      TEXT NOT NULL DEFAULT '',
    order_index INTEGER NOT NULL DEFAULT 0,
    plan_id     TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);


-- Bir gelistirme cabasinin tamami. Kosular bunun adimlaridir: "sartnameyi
-- analiz et" bir adim, "planla" bir adim, "T-003'u uygula" bir adim. Once
-- yalnizca kosular vardi ve hangi kosunun hangi ise ait oldugu hicbir yerde
-- yazmiyordu; yirmi kosuluk bir listede is akisi kullanicinin kafasindaydi.
CREATE TABLE IF NOT EXISTS workflows (
    id          TEXT PRIMARY KEY,
    seq         INTEGER NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    goal        TEXT NOT NULL DEFAULT '',
    brief       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'running',
    created_at  REAL NOT NULL,
    finished_at REAL
);

-- Bir is akisi hakkinda kullanici ile model arasinda gecen konusma.
-- Kalici: danisman bir sonraki soruda ne konusuldugunu bilmeli, ve
-- kullanici da kimin neyi neden degistirdigini geriye donuk okuyabilmeli.
-- Kosulardan AYRI durur; sohbet bir faz degil, is akisinin uzerine
-- konusulan yerdir.
CREATE TABLE IF NOT EXISTS workflow_chat (
    id          INTEGER PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    -- "user" ya da "assistant".
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    -- Modelin bu turda YAPTIGI degisiklikler (arac cagrilari). Metin
    -- olarak degil, JSON listesi: arayuz "sunu degistirdi" diye
    -- gosterebilsin ve kullanici sohbeti okurken etkiyi gorebilsin.
    changes     TEXT NOT NULL DEFAULT '[]',
    at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_by_workflow ON workflow_chat(workflow_id, id);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    seq         INTEGER NOT NULL,
    -- Hangi is akisinin adimi. Bos ise kosu bu sutundan onceki bir kayittir
    -- ve gecis onu hedefine gore bir is akisina baglar.
    workflow_id TEXT NOT NULL DEFAULT '',
    -- Bu kosunun ne oldugu: "T-001 · Saglik ucu", "Plan: Mobil" gibi.
    -- Projenin hedefi ayri bir alan; her kosuda ayni oldugu icin listede
    -- hangi kosunun ne yaptigini anlatmiyordu.
    title       TEXT NOT NULL DEFAULT '',
    -- Basligin CEVRILEBILIR hali: sozluk anahtari + parametreleri. Yazilmis
    -- metin dil degistirdiginizde oldugu gibi kalir; arayuz once bunlara
    -- bakar, yoksa `title`a duser.
    title_key   TEXT NOT NULL DEFAULT '',
    title_args  TEXT NOT NULL DEFAULT '{}',
    goal        TEXT NOT NULL DEFAULT '',
    brief       TEXT NOT NULL DEFAULT '',
    phases      TEXT NOT NULL DEFAULT '[]',
    -- Kosunun NEYI kosturdugu. Fazlar tek basina yetmiyor: ayni [ingest,
    -- implement] listesi "T-014'u yap" da olabilir "sirada ne varsa yap"
    -- da. Bu iki sutun olmadan basarisiz bir kosuyu sadik bicimde tekrar
    -- calistirmak imkansizdi -- tek bir gorev icin acilan kosu, tekrarda
    -- hazir olan BUTUN gorevleri kosardi.
    task_key    TEXT NOT NULL DEFAULT '',
    plan_id     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'running',
    error       TEXT NOT NULL DEFAULT '',
    cost_usd    REAL NOT NULL DEFAULT 0,
    started_at  REAL NOT NULL,
    finished_at REAL
);

-- Faz durumu projeye aittir ve her tekrar kosuda uzerine yazilir; bu tablo
-- *kosuya* aittir. "Ikinci kosuda mimari ne kadar surdu" sorusunun cevabi
-- ancak burada durur.
CREATE TABLE IF NOT EXISTS run_steps (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT NOT NULL,
    ordinal     INTEGER NOT NULL,
    phase       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    summary     TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    cost_usd    REAL NOT NULL DEFAULT 0,
    started_at  REAL,
    finished_at REAL,
    UNIQUE(run_id, phase)
);

CREATE INDEX IF NOT EXISTS run_steps_by_run ON run_steps(run_id, ordinal);

CREATE TABLE IF NOT EXISTS artifacts (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL DEFAULT 'other',
    path       TEXT NOT NULL,
    summary    TEXT NOT NULL DEFAULT '',
    run_id     TEXT NOT NULL DEFAULT '',
    phase      TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
"""


def _loads_list(raw: str) -> list[str]:
    """Bozuk JSON sohbeti dusurmemeli; en fazla o satirin etiketi kaybolur."""
    try:
        veri = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [str(x) for x in veri] if isinstance(veri, list) else []


def _surec_yasiyor(pid: int) -> bool:
    """Kaydin sahibi surec hala ayakta mi?

    `pid = 0` bu sutundan ONCEKI bir kayittir: sahibi bilinmiyor, eski
    davranis uygulanir ve yetim sayilir.
    """
    if pid <= 0:
        return False
    from ..process import process_alive

    return process_alive(pid)


class ProjectState:
    """Proje hafizasi. Tum yazmalar aninda commit edilir."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Eski veritabanlarini gunceller.

        `CREATE TABLE IF NOT EXISTS` var olan bir tabloyu degistirmez; bu yuzden
        sonradan eklenen sutunlar burada tek tek kontrol edilir.
        """
        for table, column, ddl in (
            ("tasks", "lane", "ALTER TABLE tasks ADD COLUMN lane TEXT NOT NULL DEFAULT 'backend'"),
            ("tasks", "plan_id", "ALTER TABLE tasks ADD COLUMN plan_id TEXT NOT NULL DEFAULT ''"),
            ("artifacts", "run_id",
             "ALTER TABLE artifacts ADD COLUMN run_id TEXT NOT NULL DEFAULT ''"),
            ("artifacts", "phase",
             "ALTER TABLE artifacts ADD COLUMN phase TEXT NOT NULL DEFAULT ''"),
            ("runs", "title",
             "ALTER TABLE runs ADD COLUMN title TEXT NOT NULL DEFAULT ''"),
            ("phase_state", "goal",
             "ALTER TABLE phase_state ADD COLUMN goal TEXT NOT NULL DEFAULT ''"),
            ("runs", "workflow_id",
             "ALTER TABLE runs ADD COLUMN workflow_id TEXT NOT NULL DEFAULT ''"),
            ("runs", "title_key",
             "ALTER TABLE runs ADD COLUMN title_key TEXT NOT NULL DEFAULT ''"),
            ("runs", "title_args",
             "ALTER TABLE runs ADD COLUMN title_args TEXT NOT NULL DEFAULT '{}'"),
            ("runs", "task_key",
             "ALTER TABLE runs ADD COLUMN task_key TEXT NOT NULL DEFAULT ''"),
            ("runs", "plan_id",
             "ALTER TABLE runs ADD COLUMN plan_id TEXT NOT NULL DEFAULT ''"),
            # Kosuyu/gorevi YURUTEN surecin kimligi. Yetim toplama bunsuz
            # "acilista hicbir sey kosmuyor" varsayimina dayaniyordu ve o
            # varsayim ikinci bir surec ayni calisma alanini actiginda
            # yanlisti: calisan bir kosu yetim sanilip kapatiliyordu.
            ("runs", "pid",
             "ALTER TABLE runs ADD COLUMN pid INTEGER NOT NULL DEFAULT 0"),
            ("tasks", "pid",
             "ALTER TABLE tasks ADD COLUMN pid INTEGER NOT NULL DEFAULT 0"),
        ):
            existing = {
                row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                log.info(t("setup.migration", table=table, column=column))
                self._conn.execute(ddl)

        # Indeksler gecisten SONRA kurulur. Sema betigine konsalardi, sutunu
        # henuz olmayan eski bir veritabaninda "no such column" ile acilis
        # cokerdi -- yani mevcut her proje yukseltmede kirilirdi.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS tasks_by_plan ON tasks(plan_id, order_index)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS runs_by_workflow ON runs(workflow_id, seq)"
        )
        self._adopt_orphan_runs()

    def _adopt_orphan_runs(self) -> None:
        """Is akisi olmayan kosulari hedeflerine gore gruplar.

        Bu sutundan onceki kosularin hangi ise ait oldugu hicbir yerde
        yazmiyor. Elde tek ipucu hedefleri: ayni hedefle kosanlar ayni
        gelistirme cabasinin adimlariydi. Kosu numarasi sirasi korunur ki
        gecmis is akislari da dogru sirayla gorunsun.
        """
        rows = self._conn.execute(
            "SELECT id, goal FROM runs WHERE workflow_id = '' ORDER BY seq ASC"
        ).fetchall()
        if not rows:
            return

        by_goal: dict[str, str] = {}
        for row in rows:
            goal = row["goal"] or ""
            key = " ".join(goal.split()).casefold()
            workflow_id = by_goal.get(key)
            if workflow_id is None:
                workflow_id = self.create_workflow(goal or "(hedefsiz)")["id"]
                by_goal[key] = workflow_id
            self._conn.execute(
                "UPDATE runs SET workflow_id = ? WHERE id = ?", (workflow_id, row["id"])
            )
        self._conn.commit()
        log.info(
            t("run.workflow_migration", runs=len(rows), workflows=len(by_goal))
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ProjectState:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Proje meta verisi
    # ------------------------------------------------------------------ #
    def set_meta(self, key: str, value: Any) -> None:
        self._conn.execute(
            "INSERT INTO project (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False, default=str)),
        )
        self._conn.commit()

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute("SELECT value FROM project WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    # ------------------------------------------------------------------ #
    # Fazlar
    # ------------------------------------------------------------------ #
    def phase_status(self, phase: Phase | str) -> PhaseState:
        name = str(phase)
        row = self._conn.execute("SELECT * FROM phase_state WHERE phase = ?", (name,)).fetchone()
        if row is None:
            return PhaseState(phase=name)
        return PhaseState(
            phase=row["phase"],
            status=row["status"],
            summary=row["summary"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            cost_usd=row["cost_usd"],
            goal=row["goal"],
        )

    def start_phase(self, phase: Phase | str) -> None:
        self._conn.execute(
            "INSERT INTO phase_state (phase, status, started_at) VALUES (?, 'running', ?) "
            "ON CONFLICT(phase) DO UPDATE SET status='running', started_at=excluded.started_at, "
            "finished_at=NULL",
            (str(phase), time.time()),
        )
        self._conn.commit()

    def finish_phase(
        self,
        phase: Phase | str,
        *,
        status: str = Status.DONE,
        summary: str = "",
        cost_usd: float = 0.0,
    ) -> None:
        # Hedefi kaydin kendisine yaz: "bu is su hedef icin yapildi".
        # Sonraki kosuda hedef degismisse tamamlanma gecersizdir.
        self._conn.execute(
            "INSERT INTO phase_state (phase, status, summary, finished_at, cost_usd, goal) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(phase) DO UPDATE SET status=excluded.status, summary=excluded.summary, "
            "finished_at=excluded.finished_at, cost_usd=phase_state.cost_usd + excluded.cost_usd, "
            "goal=excluded.goal",
            (
                str(phase), str(status), summary, time.time(), cost_usd,
                self.get_meta("goal", ""),
            ),
        )
        self._conn.commit()

    def all_phases(self) -> list[PhaseState]:
        return [self.phase_status(p) for p in Phase.ordered()]

    # ------------------------------------------------------------------ #
    # Gereksinimler
    # ------------------------------------------------------------------ #
    def add_requirement(self, req: Requirement) -> Requirement:
        self._conn.execute(
            "INSERT INTO requirements "
            "(key, title, description, category, priority, source_ref, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET title=excluded.title, "
            "description=excluded.description, category=excluded.category, "
            "priority=excluded.priority, source_ref=excluded.source_ref",
            (
                req.key, req.title, req.description, req.category,
                req.priority, req.source_ref, req.status, time.time(),
            ),
        )
        self._conn.commit()
        return req

    def list_requirements(self) -> list[Requirement]:
        rows = self._conn.execute(
            "SELECT * FROM requirements ORDER BY "
            "CASE priority WHEN 'must' THEN 0 WHEN 'should' THEN 1 "
            "WHEN 'could' THEN 2 ELSE 3 END, key"
        ).fetchall()
        return [
            Requirement(
                id=r["id"], key=r["key"], title=r["title"], description=r["description"],
                category=r["category"], priority=r["priority"],
                source_ref=r["source_ref"], status=r["status"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Bosluklar
    # ------------------------------------------------------------------ #
    def add_gap(self, gap: Gap) -> Gap:
        self._conn.execute(
            "INSERT INTO gaps "
            "(key, title, description, severity, area, recommendation, evidence, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET title=excluded.title, "
            "description=excluded.description, severity=excluded.severity, area=excluded.area, "
            "recommendation=excluded.recommendation, evidence=excluded.evidence",
            (
                gap.key, gap.title, gap.description, gap.severity, gap.area,
                gap.recommendation, gap.evidence, gap.status, time.time(),
            ),
        )
        self._conn.commit()
        return gap

    def list_gaps(self) -> list[Gap]:
        rows = self._conn.execute(
            "SELECT * FROM gaps ORDER BY "
            "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 ELSE 3 END, key"
        ).fetchall()
        return [
            Gap(
                id=r["id"], key=r["key"], title=r["title"], description=r["description"],
                severity=r["severity"], area=r["area"], recommendation=r["recommendation"],
                evidence=r["evidence"], status=r["status"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Kararlar / arastirma
    # ------------------------------------------------------------------ #
    def add_decision(self, decision: Decision) -> Decision:
        self._conn.execute(
            "INSERT INTO decisions (key, title, choice, rationale, alternatives, tradeoffs, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET title=excluded.title, choice=excluded.choice, "
            "rationale=excluded.rationale, alternatives=excluded.alternatives, "
            "tradeoffs=excluded.tradeoffs",
            (
                decision.key, decision.title, decision.choice, decision.rationale,
                decision.alternatives, decision.tradeoffs, time.time(),
            ),
        )
        self._conn.commit()
        return decision

    def list_decisions(self) -> list[Decision]:
        rows = self._conn.execute("SELECT * FROM decisions ORDER BY key").fetchall()
        return [
            Decision(
                id=r["id"], key=r["key"], title=r["title"], choice=r["choice"],
                rationale=r["rationale"], alternatives=r["alternatives"], tradeoffs=r["tradeoffs"],
            )
            for r in rows
        ]

    def add_research_note(self, note: ResearchNote) -> ResearchNote:
        self._conn.execute(
            "INSERT OR IGNORE INTO research_notes (topic, finding, url, confidence, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (note.topic, note.finding, note.url, note.confidence, time.time()),
        )
        self._conn.commit()
        return note

    def list_research_notes(self) -> list[ResearchNote]:
        rows = self._conn.execute(
            "SELECT * FROM research_notes ORDER BY topic, id"
        ).fetchall()
        return [
            ResearchNote(
                id=r["id"], topic=r["topic"], finding=r["finding"],
                url=r["url"], confidence=r["confidence"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Kullaniciya yoneltilen sorular
    # ------------------------------------------------------------------ #
    def add_question(self, question: Question) -> Question:
        """Soruyu kaydeder. Zaten cevaplanmis bir soruyu tekrar acmaz."""
        question.key = question.key.strip().upper()
        existing = self.get_question(question.key)
        if existing is not None and existing.status != "open":
            # Ajan ayni soruyu yeniden sorabilir; kullanicinin verdigi cevap kaybolmasin.
            return existing
        self._conn.execute(
            "INSERT INTO questions "
            "(key, question, why, asked_by, blocking, suggestion, status, answer, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET question=excluded.question, why=excluded.why, "
            "asked_by=excluded.asked_by, blocking=excluded.blocking, "
            "suggestion=excluded.suggestion",
            (
                question.key, question.question, question.why, question.asked_by,
                int(question.blocking), question.suggestion, question.status,
                question.answer, time.time(),
            ),
        )
        self._conn.commit()
        return question

    @staticmethod
    def _row_to_question(row: sqlite3.Row) -> Question:
        return Question(
            id=row["id"], key=row["key"], question=row["question"], why=row["why"],
            asked_by=row["asked_by"], blocking=bool(row["blocking"]),
            suggestion=row["suggestion"], status=row["status"], answer=row["answer"],
        )

    def get_question(self, key: str) -> Question | None:
        row = self._conn.execute(
            "SELECT * FROM questions WHERE key = ?", (key.strip().upper(),)
        ).fetchone()
        return self._row_to_question(row) if row else None

    def list_questions(self, status: str | None = None) -> list[Question]:
        sql = "SELECT * FROM questions"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        # Bloke edenler once: kullanici en kritik olanla karsilasmali.
        sql += " ORDER BY blocking DESC, key"
        return [self._row_to_question(r) for r in self._conn.execute(sql, params)]

    def open_blocking_questions(self) -> list[Question]:
        """Boru hattini durduran cevaplanmamis sorular."""
        return [
            self._row_to_question(r)
            for r in self._conn.execute(
                "SELECT * FROM questions WHERE status = 'open' AND blocking = 1 ORDER BY key"
            )
        ]

    def answer_question(self, key: str, answer: str) -> Question | None:
        key = key.strip().upper()
        if self.get_question(key) is None:
            return None
        self._conn.execute(
            "UPDATE questions SET status='answered', answer=?, answered_at=? WHERE key=?",
            (answer, time.time(), key),
        )
        self._conn.commit()
        return self.get_question(key)

    def skip_question(self, key: str, assumption: str = "") -> Question | None:
        """Soruyu atlar; ajanlar bundan sonra belirtilen varsayimla ilerler."""
        key = key.strip().upper()
        current = self.get_question(key)
        if current is None:
            return None
        self._conn.execute(
            "UPDATE questions SET status='skipped', suggestion=?, answered_at=? WHERE key=?",
            (assumption or current.suggestion, time.time(), key),
        )
        self._conn.commit()
        return self.get_question(key)

    # ------------------------------------------------------------------ #
    # Planlar
    # ------------------------------------------------------------------ #
    # Bir plan, adlandirilmis bagimsiz bir gorev grubudur. Ayni projede birden
    # fazla plan yasayabilir: paralel is akislari ("mobil", "backend"),
    # alternatif yaklasimlar ya da sartname degisince acilan yeni surum.
    # Gorev anahtarlari proje capinda tekildir; boylece bir planin gorevi baska
    # bir planin gorevini bekleyebilir ve hicbir referans belirsiz kalmaz.
    DEFAULT_PLAN_NAME = "Ana plan"

    def create_plan(
        self, name: str, *, description: str = "", plan_id: str = ""
    ) -> dict[str, Any]:
        import uuid as _uuid

        plan_id = plan_id or _uuid.uuid4().hex[:12]
        row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) AS n FROM plans").fetchone()
        seq = int(row["n"]) + 1
        self._conn.execute(
            "INSERT INTO plans (id, seq, name, description, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (plan_id, seq, name.strip() or f"Plan {seq}", description.strip(), time.time()),
        )
        self._conn.commit()
        created = self.get_plan(plan_id)
        assert created is not None
        return created

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return self._row_to_plan(row) if row else None

    def _row_to_plan(self, row: sqlite3.Row) -> dict[str, Any]:
        tasks = self.list_tasks(plan_id=row["id"])
        return {
            "id": row["id"],
            "seq": row["seq"],
            "name": row["name"],
            "description": row["description"],
            "status": row["status"],
            "created_at": row["created_at"],
            "tasks": len(tasks),
            "tasks_done": sum(1 for t in tasks if t.status == Status.DONE),
            "tasks_failed": sum(1 for t in tasks if t.status == Status.FAILED),
            "ready": len(self.ready_tasks(plan_id=row["id"])),
        }

    def list_plans(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM plans ORDER BY seq").fetchall()
        return [self._row_to_plan(r) for r in rows]

    def update_plan(
        self, plan_id: str, *, name: str | None = None, status: str | None = None
    ) -> dict[str, Any] | None:
        if self.get_plan(plan_id) is None:
            return None
        sets: list[str] = []
        params: list[Any] = []
        if name is not None:
            sets.append("name = ?")
            params.append(name.strip())
        if status is not None:
            sets.append("status = ?")
            params.append(str(status))
        if sets:
            params.append(plan_id)
            self._conn.execute(f"UPDATE plans SET {', '.join(sets)} WHERE id = ?", params)
            self._conn.commit()
        return self.get_plan(plan_id)

    def delete_plan(self, plan_id: str) -> int:
        """Plani ve gorevlerini siler; silinen gorev sayisini doner."""
        count = int(
            self._conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE plan_id = ?", (plan_id,)
            ).fetchone()["n"]
        )
        self._conn.execute("DELETE FROM tasks WHERE plan_id = ?", (plan_id,))
        self._conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        self._conn.commit()
        if self.get_meta("active_plan") == plan_id:
            self.set_meta("active_plan", None)
        return count

    def active_plan_id(self) -> str:
        """Yeni gorevlerin yazilacagi plan; yoksa olusturulur.

        Planlar sonradan eklendi: plansiz eski gorevler ilk cagride ana plana
        devredilir, boylece mevcut projeler bozulmadan devam eder.
        """
        current = self.get_meta("active_plan")
        if current and self.get_plan(str(current)) is not None:
            return str(current)

        row = self._conn.execute(
            "SELECT id FROM plans WHERE status = 'active' ORDER BY seq LIMIT 1"
        ).fetchone()
        if row is not None:
            self.set_meta("active_plan", row["id"])
            return str(row["id"])

        plan = self.create_plan(self.DEFAULT_PLAN_NAME)
        self._conn.execute("UPDATE tasks SET plan_id = ? WHERE plan_id = ''", (plan["id"],))
        self._conn.commit()
        self.set_meta("active_plan", plan["id"])
        return str(plan["id"])

    def set_active_plan(self, plan_id: str) -> bool:
        if self.get_plan(plan_id) is None:
            return False
        self.set_meta("active_plan", plan_id)
        return True

    # ------------------------------------------------------------------ #
    # Gorevler
    # ------------------------------------------------------------------ #
    def add_task(self, task: Task, *, plan_id: str | None = None) -> Task:
        now = time.time()
        task.key = task.key.strip().upper()
        plan = plan_id or task.plan_id or self.active_plan_id()
        task.plan_id = plan
        self._conn.execute(
            "INSERT INTO tasks "
            "(key, title, description, kind, lane, deps, files, acceptance, estimate, status, "
            " result, order_index, plan_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET title=excluded.title, "
            "description=excluded.description, kind=excluded.kind, lane=excluded.lane, "
            "deps=excluded.deps, "
            "files=excluded.files, acceptance=excluded.acceptance, estimate=excluded.estimate, "
            "order_index=excluded.order_index, plan_id=excluded.plan_id, "
            "updated_at=excluded.updated_at",
            (
                task.key, task.title, task.description, task.kind, task.lane,
                json.dumps(task.deps, ensure_ascii=False),
                json.dumps(task.files, ensure_ascii=False),
                task.acceptance, task.estimate, task.status, task.result,
                task.order_index, plan, now, now,
            ),
        )
        self._conn.commit()
        return task

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"], key=row["key"], title=row["title"], description=row["description"],
            kind=row["kind"], lane=row["lane"], deps=json.loads(row["deps"]),
            files=json.loads(row["files"]),
            acceptance=row["acceptance"], estimate=row["estimate"], status=row["status"],
            result=row["result"], order_index=row["order_index"],
            plan_id=row["plan_id"],
        )

    def list_tasks(
        self, status: str | None = None, *, plan_id: str | None = None
    ) -> list[Task]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if plan_id is not None:
            where.append("plan_id = ?")
            params.append(plan_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self._conn.execute(
            f"SELECT * FROM tasks {clause} ORDER BY order_index, key", params
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def get_task(self, key: str) -> Task | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE key = ?", (key.strip().upper(),)
        ).fetchone()
        return self._row_to_task(row) if row else None

    def update_task(self, key: str, *, status: str | None = None, result: str | None = None) -> None:
        # Anahtarlar her yerde buyuk harf; arayan tarafin normalize etmesine guvenme.
        key = key.strip().upper()
        sets, params = ["updated_at = ?"], [time.time()]
        if status is not None:
            sets.append("status = ?")
            params.append(str(status))
            # Gorevi KIM yurutuyor. `running` disina cikan gorevin sahibi
            # kalmamali; yoksa olu bir kimlik kayitta durur ve sonraki
            # yetim taramasi onu yanlis degerlendirir.
            sets.append("pid = ?")
            params.append(os.getpid() if str(status) == Status.RUNNING else 0)
        if result is not None:
            sets.append("result = ?")
            params.append(result)
        params.append(key)
        self._conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE key = ?", params)
        self._conn.commit()

# ------------------------------------------------------------------ #
    # Is akislari
    # ------------------------------------------------------------------ #
    def workflow_for_goal(self, goal: str, *, brief: str = "") -> dict[str, Any]:
        """Bu hedefin is akisini doner; yoksa acar.

        Is akisi kimligi HEDEF kimligidir: yeni bir hedef yeni bir
        gelistirme cabasidir. Ayni hedefle baslatilan kosular ayni is
        akisinin adimlari olur, hedef degisince yeni bir is akisi acilir.
        Bu, faz tamamlanmasinin hedefe baglanmasiyla ayni kural (bkz.
        `_skip_reason`) ve ikisi birbirini tutuyor.
        """
        normalized = " ".join(goal.split()).casefold()
        row = self._conn.execute(
            "SELECT * FROM workflows ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if row is not None and " ".join(row["goal"].split()).casefold() == normalized:
            if brief and row["brief"] != brief:
                self._conn.execute(
                    "UPDATE workflows SET brief = ? WHERE id = ?", (brief, row["id"])
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM workflows WHERE id = ?", (row["id"],)
                ).fetchone()
            return self._row_to_workflow(row)
        return self.create_workflow(goal, brief=brief)

    def create_workflow(self, goal: str, *, brief: str = "", title: str = "") -> dict[str, Any]:
        import uuid as _uuid

        seq_row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS n FROM workflows"
        ).fetchone()
        seq = int(seq_row["n"]) + 1
        workflow_id = _uuid.uuid4().hex[:12]
        self._conn.execute(
            "INSERT INTO workflows (id, seq, title, goal, brief, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'running', ?)",
            (workflow_id, seq, title or goal.strip(), goal, brief, time.time()),
        )
        self._conn.commit()
        log.info("Yeni is akisi #%d: %s", seq, (title or goal)[:60])
        return self.get_workflow(workflow_id) or {}

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        return self._row_to_workflow(row) if row else None

    def get_workflow_by_seq(self, seq: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM workflows WHERE seq = ?", (seq,)
        ).fetchone()
        return self._row_to_workflow(row) if row else None

    def list_workflows(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM workflows ORDER BY seq DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_workflow(r) for r in rows]

    def finish_workflow(self, workflow_id: str, *, status: str) -> None:
        self._conn.execute(
            "UPDATE workflows SET status = ?, finished_at = ? WHERE id = ?",
            (str(status), time.time(), workflow_id),
        )
        self._conn.commit()

    def workflow_runs(self, workflow_id: str) -> list[dict[str, Any]]:
        """Is akisinin adimlari: kosular, baslatildiklari sirayla."""
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE workflow_id = ? ORDER BY seq ASC", (workflow_id,)
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    @staticmethod
    def _row_to_workflow(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "seq": row["seq"],
            "title": row["title"],
            "goal": row["goal"],
            "brief": row["brief"],
            "status": row["status"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }

    # ------------------------------------------------------------------ #
    # Is akisi sohbeti
    # ------------------------------------------------------------------ #
    def update_workflow(
        self,
        workflow_id: str,
        *,
        title: str | None = None,
        goal: str | None = None,
        brief: str | None = None,
    ) -> dict[str, Any] | None:
        """Is akisinin kimligini gunceller.

        `goal` DEGISTIGINDE proje hedefi de guncellenir: fazlar hedefe
        bakarak "bu analiz baska bir projeye ait" karari veriyor
        (`_skip_reason`), ve is akisinin hedefi ile projenin hedefi
        ayrisirsa o karar yanlis tarafa duser.
        """
        alanlar: list[str] = []
        degerler: list[Any] = []
        for ad, deger in (("title", title), ("goal", goal), ("brief", brief)):
            if deger is not None:
                alanlar.append(f"{ad} = ?")
                degerler.append(deger)
        if not alanlar:
            return self.get_workflow(workflow_id)

        degerler.append(workflow_id)
        self._conn.execute(
            f"UPDATE workflows SET {', '.join(alanlar)} WHERE id = ?", degerler
        )
        if goal is not None:
            self.set_meta("goal", goal)
        if brief is not None:
            self.set_meta("brief", brief)
        self._conn.commit()
        return self.get_workflow(workflow_id)

    def add_chat_message(
        self,
        workflow_id: str,
        *,
        role: str,
        content: str,
        changes: list[str] | None = None,
    ) -> None:
        """Sohbete bir satir ekler."""
        self._conn.execute(
            "INSERT INTO workflow_chat (workflow_id, role, content, changes, at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                workflow_id,
                role,
                content,
                json.dumps(changes or [], ensure_ascii=False),
                time.time(),
            ),
        )
        self._conn.commit()

    def chat_history(self, workflow_id: str, *, limit: int = 40) -> list[dict[str, Any]]:
        """Is akisinin sohbeti, eskiden yeniye.

        `limit` SON mesajlari verir: uzun bir sohbette baglama sigan sey
        son konusulanlardir, ilk konusulanlar degil.
        """
        rows = self._conn.execute(
            "SELECT * FROM workflow_chat WHERE workflow_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (workflow_id, max(1, limit)),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "changes": _loads_list(r["changes"]),
                "at": r["at"],
            }
            for r in reversed(rows)
        ]

    def clear_chat(self, workflow_id: str) -> int:
        """Sohbeti siler; kac satir gittigini doner."""
        satir = self._conn.execute(
            "SELECT COUNT(*) FROM workflow_chat WHERE workflow_id = ?", (workflow_id,)
        ).fetchone()[0]
        self._conn.execute(
            "DELETE FROM workflow_chat WHERE workflow_id = ?", (workflow_id,)
        )
        self._conn.commit()
        return int(satir)

    def workflow_context(self, workflow_id: str, *, max_items: int = 40) -> str:
        """Is akisinin durumu, modele verilecek okunakli bicimde.

        Kapsam ayrimi BILEREK yazili: `workflows`, `runs` ve `artifacts`
        is akisina AITTIR; gereksinim, bosluk, karar ve sorular ise
        PROJE duzeyindedir ve tablolarinda is akisi kimligi tasimazlar.
        Danisman ikisini de gorur ama karistirmamali -- "bu is akisindaki
        gereksinim" diye bir sey yok, "bu projenin gereksinimi" var.
        """
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            return ""

        satirlar = [
            f"# Is akisi #{workflow['seq']}",
            f"Baslik: {workflow['title'] or '(yok)'}",
            f"Durum: {workflow['status']}",
            f"Hedef: {workflow['goal'] or '(yok)'}",
        ]
        if workflow["brief"]:
            satirlar += ["", "## Kullanicinin talimati", workflow["brief"]]

        kosular = self.workflow_runs(workflow_id)
        satirlar += ["", f"## Adimlar ({len(kosular)} kosu)"]
        plan_kimlikleri: set[str] = set()
        for kosu in kosular:
            plan_kimlikleri.add(kosu.get("plan_id") or "")
            adimlar = ", ".join(
                f"{a['phase']}={a['status']}" for a in self.run_step_rows(kosu["id"])
            )
            satirlar.append(
                f"- #{kosu['seq']} [{kosu['status']}] {kosu.get('title') or ''}"
                + (f" · {adimlar}" if adimlar else "")
            )
            if kosu.get("error"):
                satirlar.append(f"    hata: {kosu['error'][:200]}")

        kosu_kimlikleri = {k["id"] for k in kosular}
        ciktilar = [a for a in self.list_artifacts() if a.run_id in kosu_kimlikleri]
        satirlar += ["", f"## Bu is akisinin ciktilari ({len(ciktilar)})"]
        satirlar += [
            f"- {a.name} ({a.kind}) — {a.summary[:120]}" for a in ciktilar[:max_items]
        ] or ["- (yok)"]

        planlar = [p for p in self.list_plans() if p["id"] in plan_kimlikleri]
        if planlar:
            satirlar += ["", "## Bu is akisinin planlari"]
            for plan in planlar:
                gorevler = self.list_tasks(plan_id=plan["id"])
                bitmis = sum(1 for g in gorevler if g.status == Status.DONE)
                satirlar.append(
                    f"- {plan['name']} ({plan['id']}): {bitmis}/{len(gorevler)} gorev tamam"
                )

        satirlar += [
            "",
            "## Proje kayitlari (is akisina DEGIL, projeye ait)",
            self.snapshot(max_items=max_items),
        ]
        return "\n".join(satirlar)

    def reclaim_orphaned_runs(self) -> list[int]:
        """Yarida kesilmis kosulari kapatir.

        Bir kosu `running` isaretlenip surec olurse (sunucu yeniden
        baslatildi) kayit sonsuza dek "calisiyor" gorunur ve kosu listesi
        yalan soyler.

        "Acilista hicbir sey kosmuyor, dolayisiyla `running` goren her
        kayit yetimdir" DEGIL. Bu varsayim ayni calisma alanini ikinci bir
        surec actiginda yanlis, ve o senaryo desteklenen bir kullanim:
        OLCULDU -- `deerx run` terminalde calisirken `deerx serve` acmak
        (README'nin "kosuyu izlemek icin arayuzu kullanin" dedigi akis)
        calisan kosuyu `cancelled` isaretliyor, uzerine "sunucu yeniden
        baslatildi" hatasini yaziyor ve kullanici bitmis sandigi bir
        kosunun token harcamaya devam ettigini gormuyordu.

        Artik sahip surecin kimligine bakilir: yalnizca o surec OLMUSSE
        kayit yetimdir.
        """
        rows = self._conn.execute(
            "SELECT id, seq, pid FROM runs WHERE status = ?", (Status.RUNNING,)
        ).fetchall()
        yetimler = [r for r in rows if not _surec_yasiyor(int(r["pid"] or 0))]
        seqs = [int(r["seq"]) for r in yetimler]
        if seqs:
            log.info(t("run.reclaimed_log", count=len(seqs)))
            now = time.time()
            # Mesaj SQL metnine gomulu degil, PARAMETRE: gomulu oldugunda
            # hicbir zaman cevrilmiyordu ve Ingilizce arayuzde kosu listesi
            # Turkce bir hata gosteriyordu.
            for row in yetimler:
                self._conn.execute(
                    "UPDATE runs SET status = ?, finished_at = ?, error = ? "
                    "WHERE id = ?",
                    (Status.CANCELLED, now, t("run.reclaimed_error"), row["id"]),
                )
                self._conn.execute(
                    "UPDATE run_steps SET status = ?, finished_at = ? "
                    "WHERE run_id = ? AND status = ?",
                    (Status.CANCELLED, now, row["id"], Status.RUNNING),
                )
            self._conn.commit()
        return seqs

    def reclaim_orphaned_tasks(self) -> list[str]:
        """Yarida kalmis gorevleri yeniden uygulanabilir hale getirir.

        Uygulama fazi bir gorevi dagitmadan once `running` isaretler ve
        bitince cozer. Surec arada olurse (sunucu yeniden baslatildi, kosu
        oldurudu) gorev sonsuza dek `running` kalir: ne kendisi yeniden
        denenir ne de ona bagli gorevler hazir sayilir — plan tumden kilitlenir.

        Yetim karari, gorevi ustlenen surecin OLMUS olmasina baglidir --
        "kosu baslamadan cagrilir, o anda calisan gorev olamaz" varsayimina
        degil. Ikinci bir surec ayni calisma alanini actiginda o varsayim
        yanlis, ve buradaki bedeli kosu kaydindakinden agir: o anda
        uygulanan bir gorev kuyruga geri doner ve ikinci bir ajan ayni isi
        bastan yapabilir.
        """
        rows = self._conn.execute(
            "SELECT key, pid FROM tasks WHERE status = ?", (Status.RUNNING,)
        ).fetchall()
        keys = [r["key"] for r in rows if not _surec_yasiyor(int(r["pid"] or 0))]
        if keys:
            log.info(t("pipeline.reclaimed_log", count=len(keys)))
            simdi = time.time()
            for key in keys:
                self._conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE key = ?",
                    (Status.PENDING, simdi, key),
                )
            self._conn.commit()
        return keys

    def ready_tasks(self, *, plan_id: str | None = None) -> list[Task]:
        """Bagimliliklari tamamlanmis, henuz baslanmamis gorevler.

        Bagimliliklar proje capinda cozulur: bir planin gorevi baska bir
        planin gorevini bekleyebilir, cunku anahtarlar tekildir.
        """
        done = {t.key for t in self.list_tasks() if t.status == Status.DONE}
        return [
            t for t in self.list_tasks(plan_id=plan_id)
            if t.status == Status.PENDING and all(dep in done for dep in t.deps)
        ]

    def blocked_tasks(self, *, plan_id: str | None = None) -> list[Task]:
        """Bagimliligi tamamlanmadigi icin bekleyen gorevler (dongu tespiti dahil)."""
        done = {t.key for t in self.list_tasks() if t.status == Status.DONE}
        return [
            t for t in self.list_tasks(plan_id=plan_id)
            if t.status == Status.PENDING and not all(dep in done for dep in t.deps)
        ]

    # ------------------------------------------------------------------ #
    # Kosular
    # ------------------------------------------------------------------ #
    def start_run(
        self, run_id: str, *, goal: str = "", brief: str = "",
        phases: list[str] | None = None, title: str = "", workflow_id: str = "",
        title_key: str = "", title_args: dict[str, Any] | None = None,
        task_key: str = "", plan_id: str = "",
    ) -> int:
        """Yeni bir kosu acar ve sirali numarasini doner.

        Kullaniciya gosterilen sey bu numaradir (#1, #2 ...); onaltilik kimlik
        benzersizlik icindir ama insan okumaz.
        """
        # Kosu onceden ayrilmis olabilir (web arayuzu numarayi hemen gostermek
        # icin kaydi acar, sonra boru hatti ayni kimlikle buraya doner).
        # Boyle bir durumda yeni numara uretmek numarayi degistirirdi.
        existing = self._conn.execute(
            "SELECT seq FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if existing is not None:
            seq = int(existing["seq"])
        else:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS n FROM runs"
            ).fetchone()
            seq = int(row["n"]) + 1
        # `title_key` catismada BOSSA korunur: boru hatti ayni kosu kaydini
        # ikinci kez acarken (bkz. yukaridaki on-ayirma notu) anahtari
        # tasimiyor ve bos deger yazilsa ilk cagrinin verdigi anahtar
        # silinirdi.
        # `task_key` ve `plan_id` de catismada BOSSA korunur, `title_key`
        # gibi ve ayni sebeple: kaydi ilk acan web katmani bunlari biliyor,
        # ikinci kez acan boru hatti bilmeyebilir. Ustune bos yazilsaydi
        # kosunun neyi kosturdugu bilgisi tam da tekrar icin gerektigi anda
        # silinmis olurdu.
        self._conn.execute(
            "INSERT INTO runs "
            "(id, seq, workflow_id, title, title_key, title_args, goal, brief, "
            " phases, task_key, plan_id, status, started_at, pid) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, goal=excluded.goal, "
            "brief=excluded.brief, phases=excluded.phases, "
            "title_key=COALESCE(NULLIF(excluded.title_key, ''), runs.title_key), "
            "title_args=CASE WHEN excluded.title_key = '' THEN runs.title_args "
            "                ELSE excluded.title_args END, "
            "task_key=COALESCE(NULLIF(excluded.task_key, ''), runs.task_key), "
            "plan_id=COALESCE(NULLIF(excluded.plan_id, ''), runs.plan_id), "
            "workflow_id=COALESCE(NULLIF(excluded.workflow_id, ''), runs.workflow_id), "
            # Kosuyu yeniden ustlenen surec sahipligi de devralir; aksi
            # halde eski ve olu bir kimlik kaydin uzerinde kalirdi.
            "pid=excluded.pid",
            (
                run_id, seq, workflow_id, title, title_key,
                json.dumps(title_args or {}, ensure_ascii=False), goal, brief,
                json.dumps(phases or [], ensure_ascii=False),
                task_key or "", plan_id or "", time.time(), os.getpid(),
            ),
        )
        self._conn.commit()
        return seq

    def finish_run(
        self, run_id: str, *, status: str, cost_usd: float = 0.0, error: str = ""
    ) -> None:
        self._conn.execute(
            "UPDATE runs SET status=?, cost_usd=?, error=?, finished_at=? WHERE id=?",
            (str(status), cost_usd, error or "", time.time(), run_id),
        )
        self._conn.commit()

    def start_run_step(self, run_id: str, phase: Phase | str, ordinal: int) -> None:
        self._conn.execute(
            "INSERT INTO run_steps (run_id, ordinal, phase, status, started_at) "
            "VALUES (?, ?, ?, 'running', ?) "
            "ON CONFLICT(run_id, phase) DO UPDATE SET status='running', "
            "ordinal=excluded.ordinal, started_at=excluded.started_at, finished_at=NULL",
            (run_id, ordinal, str(phase), time.time()),
        )
        self._conn.commit()

    def finish_run_step(
        self,
        run_id: str,
        phase: Phase | str,
        *,
        status: str,
        summary: str = "",
        cost_usd: float = 0.0,
        error: str = "",
    ) -> None:
        self._conn.execute(
            "UPDATE run_steps SET status=?, summary=?, cost_usd=?, error=?, finished_at=? "
            "WHERE run_id=? AND phase=?",
            (str(status), summary, cost_usd, error or "", time.time(), run_id, str(phase)),
        )
        self._conn.commit()

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Kosular, en yenisi basta."""
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY seq DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def get_run_by_seq(self, seq: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE seq = ?", (seq,)).fetchone()
        return self._row_to_run(row) if row else None

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "seq": row["seq"],
            "workflow_id": row["workflow_id"],
            "title": row["title"],
            "title_key": row["title_key"],
            "title_args": json.loads(row["title_args"] or "{}"),
            "goal": row["goal"],
            "brief": row["brief"],
            "phases": json.loads(row["phases"]),
            "task_key": row["task_key"],
            "plan_id": row["plan_id"],
            "status": row["status"],
            "error": row["error"],
            # Kosuyu YURUTEN surec. Arayuz "kayitta calisiyor ama yasayan
            # bir surec var mi?" sorusunu bunsuz cevaplayamaz ve `deerx run`
            # ile baslatilmis canli bir kosuyu yarida kalmis gosterir.
            "pid": int(row["pid"] or 0),
            "cost": round(row["cost_usd"], 4),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "elapsed": round((row["finished_at"] or time.time()) - row["started_at"], 1),
        }

    def run_step_rows(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM run_steps WHERE run_id = ? ORDER BY ordinal", (run_id,)
        ).fetchall()
        return [
            {
                "phase": r["phase"],
                "ordinal": r["ordinal"],
                "status": r["status"],
                "summary": r["summary"],
                "error": r["error"],
                "cost": round(r["cost_usd"], 4),
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "elapsed": (
                    round((r["finished_at"] or time.time()) - r["started_at"], 1)
                    if r["started_at"] else None
                ),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Ciktilar
    # ------------------------------------------------------------------ #
    def add_artifact(
        self, artifact: Artifact, *, run_id: str = "", phase: str = ""
    ) -> Artifact:
        """Ciktiyi kaydeder ve uretildigi kosuya baglar.

        Ayni ad tekrar yazilirsa kayit guncellenir ve *yeni* kosuya gecer:
        cikti artik onu son ureten kosunun urunu sayilir.
        """
        artifact.run_id = run_id or artifact.run_id
        artifact.phase = phase or artifact.phase
        self._conn.execute(
            "INSERT INTO artifacts (name, kind, path, summary, run_id, phase, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, path=excluded.path, "
            "summary=excluded.summary, run_id=excluded.run_id, phase=excluded.phase, "
            "created_at=excluded.created_at",
            (
                artifact.name, artifact.kind, artifact.path, artifact.summary,
                artifact.run_id, artifact.phase, time.time(),
            ),
        )
        self._conn.commit()
        return artifact

    def list_artifacts(self, *, run_id: str | None = None) -> list[Artifact]:
        clause = "WHERE run_id = ?" if run_id is not None else ""
        params = (run_id,) if run_id is not None else ()
        rows = self._conn.execute(
            f"SELECT * FROM artifacts {clause} ORDER BY created_at", params
        ).fetchall()
        return [
            Artifact(
                id=r["id"], name=r["name"], kind=r["kind"],
                path=r["path"], summary=r["summary"],
                run_id=r["run_id"], phase=r["phase"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Ozet
    # ------------------------------------------------------------------ #
    def counts(self) -> dict[str, int]:
        def count(table: str) -> int:
            return int(self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        tasks = self.list_tasks()
        open_questions = len(self.list_questions("open"))
        return {
            "requirements": count("requirements"),
            "gaps": count("gaps"),
            "questions": count("questions"),
            "questions_open": open_questions,
            "questions_blocking": len(self.open_blocking_questions()),
            "decisions": count("decisions"),
            "research_notes": count("research_notes"),
            "tasks": len(tasks),
            "tasks_done": sum(1 for t in tasks if t.status == Status.DONE),
            "artifacts": count("artifacts"),
        }

    def snapshot(self, *, max_items: int = 60) -> str:
        """Fazlar arasi devredilen ozet. Ajanlarin sistem baglamina eklenir."""
        parts: list[str] = []

        reqs = self.list_requirements()[:max_items]
        if reqs:
            parts.append(
                "## Gereksinimler\n" + "\n".join(f"- {r.to_line()}" for r in reqs)
            )

        questions = self.list_questions()[:max_items]
        if questions:
            note = (
                "\n\nCevaplanmis sorularin cevabini dogru kabul edin; "
                "atlanmis sorularda belirtilen varsayimla ilerleyin."
            )
            body = "\n".join(q.to_line() for q in questions)
            parts.append("## Kullaniciya sorulan sorular\n" + body + note)

        gaps = self.list_gaps()[:max_items]
        if gaps:
            parts.append("## Bosluklar ve riskler\n" + "\n".join(f"- {g.to_line()}" for g in gaps))

        decisions = self.list_decisions()[:max_items]
        if decisions:
            parts.append(
                "## Mimari kararlar\n" + "\n".join(f"- {d.to_line()}" for d in decisions)
            )

        notes = self.list_research_notes()[:max_items]
        if notes:
            parts.append("## Arastirma bulgulari\n" + "\n".join(n.to_line() for n in notes))

        tasks = self.list_tasks()[:max_items]
        if tasks:
            parts.append("## Gorevler\n" + "\n".join(f"- {t.to_line()}" for t in tasks))

        artifacts = self.list_artifacts()
        if artifacts:
            parts.append(
                "## Uretilen ciktilar\n"
                + "\n".join(f"- {a.name} ({a.kind}) -> {a.path}" for a in artifacts)
            )

        return "\n\n".join(parts) if parts else "(Henuz proje hafizasinda kayit yok.)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "phases": [asdict(p) for p in self.all_phases()],
            "counts": self.counts(),
            "requirements": [asdict(r) for r in self.list_requirements()],
            "gaps": [asdict(g) for g in self.list_gaps()],
            "questions": [asdict(q) for q in self.list_questions()],
            "decisions": [asdict(d) for d in self.list_decisions()],
            "research_notes": [asdict(n) for n in self.list_research_notes()],
            "tasks": [asdict(t) for t in self.list_tasks()],
            "artifacts": [asdict(a) for a in self.list_artifacts()],
        }
