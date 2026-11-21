"""Persist desktop questions at submission and run answers on one bounded worker."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
from threading import Lock

from .provider import ProviderError
from .store import Denied


class QueueFull(Exception):
    pass


class DesktopQuestions:
    def __init__(self, store, principal, conversation, assistant_for):
        self.store = store
        self.principal = principal
        self.conversation = conversation
        self.assistant_for = assistant_for
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='desktop-question')
        self.pending = set()
        self.lock = Lock()
        with self.store.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS desktop_questions (
                snapshot TEXT PRIMARY KEY REFERENCES snapshots(id) ON DELETE CASCADE,
                question TEXT NOT NULL, skill TEXT NOT NULL, failure TEXT
            )''')
            # Recover entries saved by the old synchronous desktop bridge.
            db.execute('''INSERT OR IGNORE INTO desktop_questions(snapshot,question,skill)
                SELECT t.snapshot,t.question,COALESCE(json_extract(t.answer,'$.skill'),'trace_decision')
                FROM conversation_turns t JOIN snapshots s ON s.id=t.snapshot
                WHERE t.user=? AND s.user=? AND t.conversation=? ORDER BY t.id''',
                (self.principal, self.principal, self.conversation))

    def close(self):
        self.worker.shutdown(wait=True, cancel_futures=True)

    def remember(self, snapshot, question, skill):
        with self.store.tx() as db:
            self.store.snapshot_info(self.principal, snapshot)
            db.execute('INSERT INTO desktop_questions(snapshot,question,skill) VALUES (?,?,?)',
                       (snapshot, question, skill))

    def ensure_capacity(self):
        with self.lock:
            if len(self.pending) >= 4:
                raise QueueFull()

    def _row(self, meeting, snapshot):
        scope = self.store.snapshot_info(self.principal, snapshot)
        if scope['meeting'] != meeting:
            raise Denied('question outside meeting')
        row = self.store.db.execute(
            'SELECT * FROM desktop_questions WHERE snapshot=?', (snapshot,)
        ).fetchone()
        if row is None:
            raise Denied('question unavailable')
        return row

    def start(self, meeting, snapshot):
        with self.store.read():
            row = self._row(meeting, snapshot)
            question, skill = row['question'], row['skill']
            if self.store.private_get(self.principal, snapshot, self.conversation, question):
                return
        with self.lock:
            if snapshot in self.pending:
                return
            if len(self.pending) >= 4:
                raise QueueFull()
            self.pending.add(snapshot)
        try:
            with self.store.tx() as db:
                self._row(meeting, snapshot)
                db.execute('UPDATE desktop_questions SET failure=NULL WHERE snapshot=?', (snapshot,))
            future = self.worker.submit(self._answer, meeting, snapshot, question, skill)
        except BaseException:
            self._finished(snapshot)
            raise
        future.add_done_callback(lambda _: self._finished(snapshot))

    def _finished(self, snapshot):
        with self.lock:
            self.pending.discard(snapshot)

    def _answer(self, meeting, snapshot, question, skill):
        try:
            assistant = self.assistant_for(meeting)
            asyncio.run(assistant.ask(snapshot, question, self.conversation, skill))
            return
        except ProviderError as exc:
            failure = exc.code
        except Denied:
            failure = 'access_changed'
        except Exception as exc:
            print(f'Desktop question failed ({type(exc).__name__}).', file=sys.stderr)
            failure = 'failed'
        with self.store.tx() as db:
            db.execute('UPDATE desktop_questions SET failure=? WHERE snapshot=?', (failure, snapshot))

    def history(self, meeting):
        with self.lock:
            pending = self.pending.copy()
        with self.store.read() as db:
            self.store.metadata(self.principal, meeting)
            candidates = db.execute('''
                SELECT q.snapshot FROM desktop_questions q JOIN snapshots s ON s.id=q.snapshot
                WHERE s.user=? AND s.meeting=? ORDER BY q.rowid DESC LIMIT 30
            ''', (self.principal, meeting)).fetchall()
            result = []
            for candidate in reversed(candidates):
                snapshot = candidate['snapshot']
                try:
                    row = self._row(meeting, snapshot)
                    saved = self.store.private_get(
                        self.principal, snapshot, self.conversation, row['question']
                    )
                except Denied:
                    continue
                turn = {'id': snapshot, 'question': row['question']}
                if saved:
                    turn['answer'] = saved[0]['answer']
                elif snapshot in pending:
                    turn['pending'] = True
                else:
                    turn['resumable'] = True
                    turn['failure'] = row['failure']
                result.append(turn)
            return result
