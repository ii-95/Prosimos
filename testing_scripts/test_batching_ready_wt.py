import pytest
import pandas as pd

from bpdfr_simulation_engine.batching_processing import AndFiringRule, FiringSubRule, OrFiringRule
from bpdfr_simulation_engine.resource_calendar import parse_datetime
from testing_scripts.test_batching import (
    _verify_logs_ordered_asc,
    _verify_same_resource_for_batch,
)
from testing_scripts.test_batching_daily_hour import _arrange_and_act_base, _get_current_exec_status
from testing_scripts.test_batching import (
    _verify_logs_ordered_asc,
    assets_path,
)

TWO_HOURS_IN_SEC = 7200
THREE_HOURS_IN_SEC = 10800

data_one_week_day = [
    # Rule:             ready_wt < 3600 (3600 seconds = 1 hour)
    #                   (it's being parsed to ready_wt > 3598, cause it should be fired last_item_en_time + 3599)
    # Current state:    4 tasks waiting for the batch execution.
    #                   difference between each of them is not > 3598
    # Expected result:  firing rule is enabled at the time we check for enabled time 
    #                   enabled time of the batch equals to the enabled time of the last item in the batch + 3598 
    #                   (value dictated by rule)
    (
        "19/09/22 14:35:26",
        [
            "19/09/22 12:00:26",
            "19/09/22 12:30:26",
            "19/09/22 13:00:26",
            "19/09/22 13:30:26",
        ],
        "<",
        3600,
        True,
        [4],
        "19/09/2022 14:30:25",
    ),
    # Rule:             ready_wt > 3600 (3600 seconds = 1 hour)
    # Current state:    5 tasks waiting for the batch execution.
    # Expected result:  Firing rule is enabled for the all items and this equals to two batches to be executed.
    #                   Enabled time of the batch equals to the enabled time of the last item in each batch
    #                   (meaning, to the maximum datetime from all enabled batches).
    #                   There is difference between two activities (3d and 4th) which exceeds one hour limit,
    #                   so that's what triggers the first batch to be enabled. 
    #                   Verify that enabled_time of the batch is one second after the datetime forced by the rule.
    (
        "19/09/22 14:05:26",
        [
            "19/09/22 10:00:26",
            "19/09/22 10:00:26",
            "19/09/22 10:30:26",
            "19/09/22 12:00:26",
            "19/09/22 12:30:26",
        ],
        ">",
        3600, # one hour
        True,
        [3, 2],
        "19/09/2022 11:30:27",
    ),
    # Rule:             ready_wt >= 3600 (3600 seconds = 1 hour)
    # Current state:    4 tasks waiting for the batch execution.
    # Expected result:  Firing rule is enabled for the all items and this equals to one batch enabled.
    #                   All activities have difference of less than one hour,
    #                   that's why the rule were not satisfied at that point somewhere.
    #                   Verify that enabled_time of the batch equals exactly to the datetime forced by the rule.
    (
        "19/09/22 14:05:26",
        [
            "19/09/22 11:00:26",
            "19/09/22 11:30:26",
            "19/09/22 12:00:26",
            "19/09/22 12:30:26",
        ],
        ">=",
        3600, # one hour
        True,
        [4],
        "19/09/2022 13:30:26",
    ),
    # Rule:             ready_wt > 3600 (3600 seconds = 1 hour)
    # Current state:    3 tasks waiting for the batch execution.
    # Expected result:  Firing rule is not enabled since no items
    #                   waiting for batch execution satisfies the rule.
    (
        "19/09/22 13:00:26",
        [
            "19/09/22 12:00:26",
            "19/09/22 12:15:26",
            "19/09/22 12:30:26",
        ],
        ">",
        3600, # one hour
        False,
        None,
        None,
    ),
]


@pytest.mark.parametrize(
    "curr_enabled_at_str, enabled_datetimes, sign_ready_wt, ready_wt_value_sec, expected_is_true, expected_batch_size, expected_start_time_from_rule",
    data_one_week_day,
)
def test_ready_wt_rule_correct_is_true(
    curr_enabled_at_str,
    enabled_datetimes,
    sign_ready_wt,
    ready_wt_value_sec,
    expected_is_true,
    expected_batch_size,
    expected_start_time_from_rule,
):

    # ====== ARRANGE ======
    firing_sub_rule_1 = FiringSubRule(
        "ready_wt", sign_ready_wt, ready_wt_value_sec
    )
    firing_rule_1 = AndFiringRule([firing_sub_rule_1])
    firing_rule_1.init_ready_wt_boundaries_if_any()
    rule = OrFiringRule([firing_rule_1])

    current_exec_status = _get_current_exec_status(curr_enabled_at_str, enabled_datetimes)

    # ====== ACT & ASSERT ======
    (is_true, batch_spec, start_time_from_rule) = rule.is_true(current_exec_status)
    assert expected_is_true == is_true
    assert expected_batch_size == batch_spec

    if expected_start_time_from_rule == None:
        assert expected_start_time_from_rule == start_time_from_rule
    else:
        start_dt = start_time_from_rule.strftime("%d/%m/%Y %H:%M:%S")
        assert expected_start_time_from_rule == start_dt

@pytest.mark.parametrize('execution_number', range(5))
def test_only_high_boundary_correct_distance_between_batches_and_inside(execution_number, assets_path):
    """
    Input:      6 process cases are being generated. A new case arrive every 3 hours.
                Batched task are executed in parallel.
    Expected:   Batched task are executed only when the difference between newly arrived 
                and the previous one exceeds the range of 5 hours.
                Since we generate 6 new cases with the arrival case of 3 hours,
                the batch will not get executed during the generation of those cases.
                Batch of 6 activities will be enabled after 5 hours of the last arrived activity
                (the one which supposed to be in the batch).
    Verified:   The start_time of the appropriate grouped D task.
                The number of tasks in every executed batch.
                The resource which executed the batch is the same for all tasks in the batch.
                The start_time of all logs files is being sorted by ASC.
    """

    # ====== ARRANGE & ACT ======
    firing_rules = [
        [
            {"attribute": "ready_wt", "comparison": "<", "value": TWO_HOURS_IN_SEC}, # 2 hours
        ]
    ]

    sim_logs = assets_path / "batch_logs.csv"

    start_string = "2022-09-29 23:45:30.035185+03:00"
    start_date = parse_datetime(start_string, True)

    total_num_cases = 10
    _arrange_and_act_exp(assets_path, firing_rules, start_string, total_num_cases)

    # ====== ASSERT ======
    df = pd.read_csv(sim_logs)
    df["enable_time"] = pd.to_datetime(df["start_time"], errors="coerce")
    logs_d_task = df[df["activity"] == "D"]
    grouped_by_start_and_resource = logs_d_task.groupby(by=["start_time", "resource"])

    prev_row_value = None
    total_count_activities = 0

    # verify time distance between tasks inside the batch is less
    # than the one specified in the rule (2 hours)
    for _, group in grouped_by_start_and_resource:
        for index, row in group.iterrows():
            total_count_activities += 1
            if prev_row_value == None:
                prev_row_value = row["enable_time"]
                continue
        
            diff = (row["enable_time"] - prev_row_value).seconds
            assert (
                diff < TWO_HOURS_IN_SEC,
            ), f"The diff between two rows {index} and {index-1} does not follow the rule. \
                    Expected TWO_HOURS_IN_SEC sec, but was {diff}"

            prev_row_value = row["enable_time"]

    assert total_count_activities == total_num_cases, \
        f"Total number of batched activites should be equal to total num of generated use cases. \
            Expected {total_num_cases}, but was {total_count_activities}"

    # verify that distance between pair of batch
    # is greater that the one specified in the rule (2 hours)
    first_last_enable_times = pd \
        .concat([
            grouped_by_start_and_resource.head(1),
            grouped_by_start_and_resource.tail(1)
        ]) \
        .reset_index(drop=True)

    prev_row_enable_time = None
    for index, item in first_last_enable_times.iterrows():
        # verify that enabled and start time are not equal
        # since we should wait at least two hours
        assert ( 
            item["enable_time"] != item["start_time"]
        ), f"The enable_time and start_time should not be equal (row {index+2})."

        if index in [0, 1]:
            prev_row_enable_time = item["enable_time"]
            continue

        diff = (item["enable_time"] - prev_row_enable_time).seconds
        
        assert (
            diff > TWO_HOURS_IN_SEC,
        ), f"The diff between two rows {index+2} and {index+1} does not follow the rule. \
                Expected greater than {TWO_HOURS_IN_SEC} sec, but was {diff}"
        
        prev_row_enable_time = item["enable_time"]

    # verify that column 'start_time' is ordered ascendingly
    _verify_logs_ordered_asc(df, start_date.tzinfo)


data_range = [
    (
        "19/09/22 15:35:26",
        [
            "19/09/22 12:00:26",
            "19/09/22 12:30:26",
            "19/09/22 13:00:26",
            "19/09/22 13:30:26",
        ],
        (">", TWO_HOURS_IN_SEC),
        ("<", THREE_HOURS_IN_SEC),
        True,
        [4],
        "19/09/2022 15:30:27"
    ),
    (
        "19/09/22 14:35:26",
        [
            "19/09/22 12:00:26",
        ],
        (">", TWO_HOURS_IN_SEC),
        ("<", THREE_HOURS_IN_SEC),
        False,
        None,
        None,
    ),
    (
        "19/09/22 15:35:26",
        [
            "19/09/22 12:00:26",
        ],
        (">", TWO_HOURS_IN_SEC),
        ("<", THREE_HOURS_IN_SEC),
        True,
        [1],
        "19/09/2022 15:00:25"
    ),
    (
        "19/09/22 21:15:26",
        [
            "19/09/22 12:00:26",
            "19/09/22 14:45:26",
            "19/09/22 17:30:26",
        ],
        (">", TWO_HOURS_IN_SEC),
        ("<", THREE_HOURS_IN_SEC),
        True,
        [2, 1],
        "19/09/2022 16:45:27"
    ),
]


@pytest.mark.parametrize(
    "curr_enabled_at_str, enabled_datetimes, first_rule, second_rule, expected_is_true, expected_batch_size, expected_start_time_from_rule",
    data_range,
)
def test_range_correct_is_true(
    curr_enabled_at_str,
    enabled_datetimes,
    first_rule,
    second_rule,
    expected_is_true,
    expected_batch_size,
    expected_start_time_from_rule
):

    # ====== ARRANGE ======
    fr_sign, fr_value = first_rule
    sr_sign, sr_value = second_rule

    firing_sub_rule_1 = FiringSubRule(
        "ready_wt", fr_sign, fr_value
    )
    firing_sub_rule_2 = FiringSubRule(
        "ready_wt", sr_sign, sr_value
    )
    firing_rule_1 = AndFiringRule([firing_sub_rule_1, firing_sub_rule_2])
    firing_rule_1.init_ready_wt_boundaries_if_any()
    rule = OrFiringRule([firing_rule_1])

    current_exec_status = _get_current_exec_status(curr_enabled_at_str, enabled_datetimes)

    # ====== ACT & ASSERT ======
    (is_true, batch_spec, start_time_from_rule) = rule.is_true(current_exec_status)
    assert expected_is_true == is_true
    assert expected_batch_size == batch_spec

    if expected_start_time_from_rule == None:
        assert expected_start_time_from_rule == start_time_from_rule
    else:
        start_dt = start_time_from_rule.strftime("%d/%m/%Y %H:%M:%S")
        assert expected_start_time_from_rule == start_dt


@pytest.mark.parametrize('execution_number', range(5))
def test_range_correct_distance_between_batches_and_inside(execution_number, assets_path):
    # ====== ARRANGE & ACT ======
    firing_rules = [
        [
            {"attribute": "ready_wt", "comparison": ">", "value": TWO_HOURS_IN_SEC},
            {"attribute": "ready_wt", "comparison": "<", "value": THREE_HOURS_IN_SEC},
        ]
    ]

    sim_logs = assets_path / "batch_logs.csv"

    start_string = "2022-09-29 23:45:30.035185+03:00"
    start_date = parse_datetime(start_string, True)

    total_num_cases = 20
    _arrange_and_act_exp(assets_path, firing_rules, start_string, total_num_cases)

    # ====== ASSERT ======
    df = pd.read_csv(sim_logs)
    df["enable_time"] = pd.to_datetime(df["enable_time"], errors="coerce")
    df["start_time"] = pd.to_datetime(df["start_time"], errors="coerce")

    logs_d_task = df[df["activity"] == "D"]

    grouped_by_start_and_resource = logs_d_task.groupby(by=["start_time", "resource"])

    # verify time distance between tasks inside the batch is less
    # than the one specified in the rule (2 hours)
    _verify_distance_inside_batch(grouped_by_start_and_resource, TWO_HOURS_IN_SEC, total_num_cases)

    # verify that the same resource execute the whole batch
    grouped_by_start = logs_d_task.groupby(by=["start_time"])
    for _, group in grouped_by_start:
        _verify_same_resource_for_batch(group["resource"])

    # verify that difference between start and enable time is taken from 
    # the high boundary for batch with one item 
    batches_with_one_task = grouped_by_start_and_resource.filter(lambda x: len(x) == 1)
    for index, item in batches_with_one_task.iterrows():
        difference = (item['start_time'] - item['enable_time']).seconds
        expected_difference = THREE_HOURS_IN_SEC - 1
        assert difference == expected_difference, \
            f"For batches with one task, the difference between start_date and end_date \
                should be equal to {expected_difference}, but was {difference}"

    # verify that distance between pair of batch
    # is greater that the one specified in the rule (2 hours)
    _verify_distance_ouside_batch(grouped_by_start_and_resource, TWO_HOURS_IN_SEC, THREE_HOURS_IN_SEC)

    # verify that column 'start_time' is ordered ascendingly
    _verify_logs_ordered_asc(df, start_date.tzinfo)

def _verify_distance_inside_batch(grouped_by_start_and_resource, upper_limit, total_num_cases):
    total_count_activities = 0
    prev_row_value = None

    for _, group in grouped_by_start_and_resource:
        for index, row in group.iterrows():
            total_count_activities += 1
            if prev_row_value == None:
                prev_row_value = row["enable_time"]
                continue
        
            diff = (row["enable_time"] - prev_row_value).seconds
            assert (
                diff < upper_limit,
            ), f"The diff between two rows {index} and {index-1} does not follow the rule. \
                    Expected TWO_HOURS_IN_SEC sec, but was {diff}"

            prev_row_value = row["enable_time"]

    assert total_count_activities == total_num_cases, \
        f"Total number of batched activites should be equal to total num of generated use cases. \
            Expected {total_num_cases}, but was {total_count_activities}"


def _verify_distance_ouside_batch(grouped_by_start_and_resource, low_boundary, high_boundary):
    first_last_enable_times = grouped_by_start_and_resource \
        .agg(['first', 'last']) \
        .stack() \
        .reset_index()

    prev_row_enable_time = first_last_enable_times["enable_time"][1]
    for index, item in first_last_enable_times.iloc[2:].iterrows():
        if index % 2 != 0:
            prev_row_enable_time = item["enable_time"]
            continue

        # verify that enabled and start time are not equal
        # since we should wait at least two hours
        assert ( 
            item["enable_time"] != item["start_time"]
        ), f"The enable_time and start_time should not be equal (row {index+2})."

        diff = (item["enable_time"] - prev_row_enable_time).seconds
        
        assert (
            low_boundary < diff > high_boundary,
        ), f"The diff between two rows {index+2} and {index+1} does not follow the rule. \
                Expected between {low_boundary} and {high_boundary}, but was {diff}"
        
        prev_row_enable_time = item["enable_time"]

def _arrange_and_act_exp(assets_path, firing_rules, start_date, num_cases):
    arrival_distr = {
        "distribution_name": "expon",
        "distribution_params": [
            { "value": 0 },
            { "value": 7200.0 },
            { "value": 0.0 },
            { "value": 100000.0 },
        ]
    }

    _arrange_and_act_base(assets_path, firing_rules, start_date, num_cases, arrival_distr)
