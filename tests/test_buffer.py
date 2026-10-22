from signalbloom_agent.buffer import FILE_NAME, ReadingBuffer, cap_for


def reading(i: int) -> dict:
    return {"measured_at": f"2026-11-03T14:{i // 60:02d}:{i % 60:02d}+13:00", "latency_ms": i}


def test_cap_is_24_hours_of_readings():
    assert cap_for(30) == 2880
    assert cap_for(7) == 12343  # rounds up


def test_add_and_remove_survive_a_restart(tmp_path):
    buffer = ReadingBuffer(tmp_path, 100)
    for i in range(5):
        buffer.add(reading(i))
    buffer.remove_oldest(2)

    again = ReadingBuffer(tmp_path, 100)
    assert len(again) == 3
    assert [r["latency_ms"] for r in again.oldest(10)] == [2, 3, 4]


def test_oldest_returns_copies_in_order(tmp_path):
    buffer = ReadingBuffer(tmp_path, 100)
    for i in range(5):
        buffer.add(reading(i))
    batch = buffer.oldest(2)
    assert [r["latency_ms"] for r in batch] == [0, 1]
    batch[0]["latency_ms"] = 999
    assert buffer.oldest(1)[0]["latency_ms"] == 0


def test_full_buffer_drops_oldest(tmp_path):
    buffer = ReadingBuffer(tmp_path, 3)
    dropped = [buffer.add(reading(i)) for i in range(5)]
    assert dropped == [0, 0, 0, 1, 1]
    assert [r["latency_ms"] for r in buffer.oldest(10)] == [2, 3, 4]


def test_smaller_cap_on_restart_keeps_newest(tmp_path):
    buffer = ReadingBuffer(tmp_path, 10)
    for i in range(6):
        buffer.add(reading(i))
    assert [r["latency_ms"] for r in ReadingBuffer(tmp_path, 2).oldest(10)] == [4, 5]


def test_unreadable_lines_are_skipped(tmp_path):
    (tmp_path / FILE_NAME).write_text(
        '{"measured_at": "a", "latency_ms": 1}\n'
        "not json\n"
        '{"no_time": true}\n'
        "\n"
        '{"measured_at": "b", "latency_ms": 2}\n',
        encoding="utf-8",
    )
    buffer = ReadingBuffer(tmp_path, 10)
    assert len(buffer) == 2
    assert buffer.bad_lines == 2


def test_creates_data_dir_and_leaves_no_temp_files(tmp_path):
    data_dir = tmp_path / "new" / "data"
    buffer = ReadingBuffer(data_dir, 10)
    buffer.add(reading(1))
    buffer.remove_oldest(1)
    assert [p.name for p in data_dir.iterdir()] == [FILE_NAME]
