from app.metrics import Registry, escape, render_labels, top


def test_help_and_type_emitted_once_per_family():
    reg = Registry()
    # simulates two zones collected in sequence into the same family
    reg.add("cf_requests", "help", "gauge", [({"zone": "a"}, 1)])
    reg.add("cf_requests", "help", "gauge", [({"zone": "b"}, 2)])
    text = reg.text()
    assert text.count("# HELP cf_requests") == 1
    assert text.count("# TYPE cf_requests") == 1
    assert 'cf_requests{zone="a"} 1' in text
    assert 'cf_requests{zone="b"} 2' in text


def test_duplicate_label_sets_are_dropped():
    reg = Registry()
    reg.add("cf_x", "h", "gauge", [({"zone": "a"}, 1), ({"zone": "a"}, 9)])
    assert reg.text().count('cf_x{zone="a"}') == 1


def test_empty_samples_do_not_create_a_family():
    reg = Registry()
    reg.add("cf_nothing", "h", "gauge", [])
    assert "cf_nothing" not in reg.text()


def test_metric_without_labels_has_no_braces():
    reg = Registry()
    reg.add("cf_up", "h", "gauge", [({}, 1)])
    assert "cf_up 1" in reg.text()


def test_exposition_ends_with_newline():
    reg = Registry()
    reg.add("cf_up", "h", "gauge", [({}, 1)])
    assert reg.text().endswith("\n")


def test_escape_handles_quotes_backslashes_and_newlines():
    assert escape('a"b') == 'a\\"b'
    assert escape("a\\b") == "a\\\\b"
    assert escape("a\nb") == "a b"


def test_labels_are_sorted_for_stable_output():
    assert render_labels({"z": 1, "a": 2}) == 'a="2",z="1"'


def test_top_sorts_descending_and_limits():
    assert top({"a": 1, "b": 5, "c": 3}, 2) == [("b", 5), ("c", 3)]


def test_top_is_deterministic_on_ties():
    assert top({"b": 1, "a": 1}, 2) == [("a", 1), ("b", 1)]
