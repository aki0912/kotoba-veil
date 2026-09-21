from benchmarks.compare_performance import summarize


def test_comparison_weights_processes_equally_and_reports_slowdown():
    def run(variant, pdf, synthetic):
        return {"variant": variant, "model_load_ms": 0, "first_inference_ms": 1,
                "pdf_detection_ms": pdf, "synthetic_total_ms": synthetic,
                "pipeline": [{"extract_ms": 1, "detect_ms": value,
                              "mask_ms": 2, "total_ms": value + 3} for value in pdf]}

    result = summarize([
        run("before", [1, 1, 1], 10), run("after", [10], 20),
        run("after", [20], 40), run("before", [100], 30),
    ])
    # Three repetitions in one process must not outweigh the second process.
    assert result["pdf_detection_ms"]["before"]["median_ms"] == 50.5
    assert result["pipeline_total_ms"]["before"]["median_ms"] == 53.5
    assert result["synthetic_total_ms"]["delta_ms"] == 10
    assert result["synthetic_total_ms"]["change_percent"] == 50
    assert result["model_load_ms"]["change_percent"] is None
