from fsbench.order_contract import window_starts_are_ordered


def test_equal_window_starts_allow_reverse_identifier_order():
    stops = [
        {"order_id": "z", "window_start": "2020-01-01T10:00:00Z"},
        {"order_id": "a", "window_start": "2020-01-01T10:00:00+00:00"},
    ]
    assert window_starts_are_ordered(stop["window_start"] for stop in stops)


def test_different_offset_representations_compare_as_instants():
    assert window_starts_are_ordered(
        ["2020-01-01T11:00:00+01:00", "2020-01-01T10:00:00Z"]
    )


def test_identifier_order_cannot_excuse_decreasing_window_starts():
    stops = [
        {"order_id": "a", "window_start": "2020-01-01T11:00:00Z"},
        {"order_id": "z", "window_start": "2020-01-01T10:00:00Z"},
    ]
    assert not window_starts_are_ordered(stop["window_start"] for stop in stops)
