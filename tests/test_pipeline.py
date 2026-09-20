import json
import threading
import time

import pytest

from markmap_pipeline.extract import Turn
from markmap_pipeline.sources import SourceState
from markmap_pipeline.backends import BackendError
from markmap_pipeline.pipeline import LockError, Pipeline, PipelineState, acquire_lock, load_state, save_state
from markmap_pipeline.updater import UpdateResult


class FakeSource:
    def __init__(self, batches):
        self.batches = list(batches)
        self.polls = 0

    def poll(self, state):
        self.polls += 1
        if not self.batches:
            return [], state
        turns = self.batches.pop(0)
        return turns, SourceState(path="/s.jsonl", offset=state.offset + 100)


class FakeUpdater:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def update(self, turns):
        self.calls.append(list(turns))
        result = self.results.pop(0) if self.results else UpdateResult(status="updated", attempts=1)
        if isinstance(result, Exception):
            raise result
        return result


class TestPipeline:
    def test_run_update_advances_state_and_calls_updater(self, tmp_path):
        state_path = tmp_path / "state.json"
        source = FakeSource([[Turn("user", "a")]])
        updater = FakeUpdater()
        pipeline = Pipeline(source, updater, state_path)
        result = pipeline.run_update()
        assert result.status == "updated"
        assert updater.calls == [[Turn("user", "a")]]
        saved = load_state(state_path)
        assert saved.source.offset == 100
        assert saved.updated_at is not None

    def test_no_new_turns_saves_position_but_skips_updater(self, tmp_path):
        state_path = tmp_path / "state.json"
        pipeline = Pipeline(FakeSource([]), FakeUpdater(), state_path)
        result = pipeline.run_update()
        assert result.status == "noop"
        assert pipeline.updater.calls == []

    def test_backend_error_keeps_old_state_for_retry(self, tmp_path, caplog):
        state_path = tmp_path / "state.json"
        source = FakeSource([[Turn("user", "a")]])
        updater = FakeUpdater([BackendError("boom")])
        pipeline = Pipeline(source, updater, state_path)
        result = pipeline.run_update()
        assert result.status == "error"
        assert "boom" in result.message
        assert load_state(state_path) == PipelineState()  # the offset must not advance
        assert "boom" in caplog.text

    def test_state_is_loaded_from_disk_on_each_run(self, tmp_path):
        state_path = tmp_path / "state.json"
        seen = []

        class Src(FakeSource):
            def poll(self, state):
                seen.append(state.offset)
                return super().poll(state)

        pipeline = Pipeline(Src([[Turn("user", "a")], [Turn("user", "b")]]), FakeUpdater(), state_path)
        pipeline.run_update()
        pipeline.run_update()
        assert seen == [0, 100]


class TestLock:
    def test_second_lock_on_same_output_fails(self, tmp_path):
        output = tmp_path / "mindmap.md"
        first = acquire_lock(output)
        with pytest.raises(LockError):
            acquire_lock(output)
        first.release()
        second = acquire_lock(output)
        second.release()

    def test_different_outputs_do_not_conflict(self, tmp_path):
        a = acquire_lock(tmp_path / "a.md")
        b = acquire_lock(tmp_path / "b.md")
        a.release()
        b.release()

    def test_lock_file_is_created_next_to_output(self, tmp_path):
        output = tmp_path / "sub" / "mindmap.md"
        lock = acquire_lock(output)
        assert (tmp_path / "sub" / ".mindmap.md.lock").exists()
        lock.release()


class TestLockWait:
    def test_waits_until_lock_is_released(self, tmp_path):
        output = tmp_path / "mindmap.md"
        first = acquire_lock(output)
        threading.Timer(0.3, first.release).start()
        started = time.time()
        second = acquire_lock(output, timeout=2.0)
        assert 0.2 <= time.time() - started < 1.5
        second.release()

    def test_times_out_when_lock_is_held(self, tmp_path):
        output = tmp_path / "mindmap.md"
        first = acquire_lock(output)
        try:
            started = time.time()
            with pytest.raises(LockError) as exc:
                acquire_lock(output, timeout=0.3)
            assert time.time() - started >= 0.3
            assert "timed out" in str(exc.value).lower()
        finally:
            first.release()

class TestPipelineState:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / ".mindmap_w.state.json"
        state = PipelineState(source=SourceState(path="/a.jsonl", offset=10), updated_at="2026-09-18T00:00:00Z")
        save_state(path, state)
        assert load_state(path) == state
        assert json.loads(path.read_text())["source"]["offset"] == 10

    def test_missing_file_gives_default(self, tmp_path):
        assert load_state(tmp_path / "nope.json") == PipelineState()

    def test_corrupt_file_gives_default(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        assert load_state(path) == PipelineState()

    def test_save_is_atomic_no_temp_left(self, tmp_path):
        path = tmp_path / "s.json"
        save_state(path, PipelineState())
        assert [p.name for p in tmp_path.iterdir()] == ["s.json"]
