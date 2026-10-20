"""代码版 Critic：数字提取和核对。它错了，needs_human_review 就是假的，所以测细一点。"""
import pytest

from chemq import critic


@pytest.mark.parametrize("text, expected", [
    ("2026年3月单耗 134.75 kgce/t", ["134.75"]),                  # 日期不算
    ("2026-02 是 130.32，2026-03 是 134.75", ["130.32", "134.75"]),
    ("P01 合成氨装置开车率 51.61%", ["51.61"]),                     # P01 是编号
    ("排名第 1 的是双氧水装置", []),                                  # 一位整数、无单位：排名
    ("停了 3 次", ["3"]),                                            # 一位整数 + 单位：要核对
    ("总产量 74,178.7 吨", ["74,178.7"]),
    ("3月5日到3月19日大修", []),
])
def test_numbers_in_text(text, expected):
    assert critic.numbers_in_text(text) == expected


def test_rounding_and_thousands_separator_match():
    rows = [{"装置": "双氧水装置", "v": 142.1834}, {"产量": "74178.70"}]
    assert critic.check("双氧水 142.18，总产量 74,178.7 吨", rows).ok


def test_injected_number_is_caught():
    """破坏实验 B：把 74178.7 改成 78000，Critic 必须抓出来。"""
    v = critic.check("8 月总产量 78000 吨", [{"v": "74178.7"}])
    assert not v.ok and v.unverified == ["78000"]


def test_numbers_from_citations_count():
    v = critic.check("单耗比 2 月高 4.43", rows=[], texts=["单耗 134.75，较上月 130.32 增加 4.43（+3.4%）"])
    assert v.ok


def test_difference_of_two_data_points_is_accepted():
    """W4 修正：两期之差放行（第一版要求差值必须在 SQL 里算，Agent 就只返回差值、丢了两期原值）。"""
    rows = [{"k": "2026-07", "v": "90.28"}, {"k": "2026-08", "v": "36.30"}]
    assert critic.check("8 月 36.30%，比 7 月下降 53.98 个百分点", rows).ok


def test_ratio_is_not_accepted():
    """比例不放行：36.30 / 90.28 ≈ 0.40，模型心算的比例要标出来。"""
    rows = [{"k": "2026-07", "v": "90.28"}, {"k": "2026-08", "v": "36.30"}]
    v = critic.check("8 月只有 7 月的 0.40 倍", rows)
    assert not v.ok and v.unverified == ["0.40"]


def test_wan_scaling_only_when_followed_by_wan():
    rows = [{"收入": "6792858.38", "回款": "2465686.77"}]
    assert critic.check("收入 679.29万元，回款 246.57万元", rows).ok


def test_ten_times_unit_error_is_caught():
    """真实发生过：Agent 把 71,779.889 吨写成"71.8 万吨"（应为 7.18 万吨），评测 exec_acc 照样满分。"""
    rows = [{"产量_t": "71779.889"}, {"产量_t": "74388.941"}]
    v = critic.check("产量在71.8～74.4万吨区间波动", rows)
    assert not v.ok and v.unverified == ["71.8", "74.4"]
    assert critic.check("产量在7.18～7.44万吨区间波动", rows).ok


def test_numbers_from_question_count():
    v = critic.check("最近6个月产量如下", rows=[], question="把最近6个月的产量按月列出来")
    assert v.ok


def test_refs_are_not_numbers():
    assert critic.numbers_in_text("检修月不考核 [KB-ENE-2026#7]") == []


def test_negative_matches_absolute():
    assert critic.check("环比下降 0.52 个百分点", [{"diff": -0.52}]).ok
