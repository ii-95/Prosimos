
import math
import pandas as pd
from scipy.stats import wasserstein_distance


class SimStats:
    def __init__(self, hour_emd_index, day_emd_index, emd_trace):
        self.hour_emd_index = hour_emd_index
        self.day_emd_index = day_emd_index
        self.emd_trace = emd_trace


def read_and_preprocess_log(event_log_path: str) -> pd.DataFrame:
    # Read from CSV
    event_log = pd.read_csv(event_log_path)
    # Transform to Timestamp bot start and end columns

    event_log['StartTimestamp'] = pd.to_datetime(event_log['StartTimestamp'], utc=True)
    event_log['EndTimestamp'] = pd.to_datetime(event_log['EndTimestamp'], utc=True)

    # Sort by end timestamp, then by start timestamp, and then by activity name
    event_log = event_log.sort_values(
        ['EndTimestamp', 'StartTimestamp', 'Activity', 'CaseID', 'Resource']
    )
    # Reset the index
    event_log.reset_index(drop=True, inplace=True)
    return event_log


def discretize_to_hour(seconds: int):
    return math.floor(seconds / 3600)


def discretize_to_day(seconds: int):
    return math.floor(seconds / 3600 / 24)


def absolute_hour_emd(event_log_1: pd.DataFrame, event_log_2: pd.DataFrame, discretize=discretize_to_hour) -> float:
    # Get the first and last dates of the log

    interval_start = min(event_log_1['StartTimestamp'].min(), event_log_2['StartTimestamp'].min())
    interval_start = interval_start.replace(minute=0, second=0, microsecond=0, nanosecond=0)
    # Discretize each instant to its corresponding "bin"
    discretized_instants_1 = []

    discretized_instants_1 += [
        discretize(difference.total_seconds()) for difference in (event_log_1['StartTimestamp'] - interval_start)
    ]
    discretized_instants_1 += [
        discretize(difference.total_seconds()) for difference in (event_log_1['EndTimestamp'] - interval_start)
    ]
    # Discretize each instant to its corresponding "bin"
    discretized_instants_2 = []

    discretized_instants_2 += [
        discretize(difference.total_seconds()) for difference in (event_log_2['StartTimestamp'] - interval_start)
    ]
    discretized_instants_2 += [
        discretize(difference.total_seconds()) for difference in (event_log_2['EndTimestamp'] - interval_start)
    ]
    # Return EMD metric

    return wasserstein_distance(discretized_instants_1, discretized_instants_2)


def trace_duration_emd(event_log_1: pd.DataFrame, event_log_2: pd.DataFrame, bin_size) -> float:
    # Get trace durations of each trace for the first log
    trace_durations_1 = []
    for case, events in event_log_1.groupby(['CaseID']):
        trace_durations_1 += [events['EndTimestamp'].max() - events['StartTimestamp'].min()]
    # Get trace durations of each trace for the second log
    trace_durations_2 = []
    for case, events in event_log_2.groupby(['CaseID']):
        trace_durations_2 += [events['EndTimestamp'].max() - events['StartTimestamp'].min()]
    # Discretize each instant to its corresponding "bin"
    min_duration = min(trace_durations_1 + trace_durations_2)
    discretized_durations_1 = [math.floor((trace_duration - min_duration) / bin_size) for trace_duration in
                               trace_durations_1]
    discretized_durations_2 = [math.floor((trace_duration - min_duration) / bin_size) for trace_duration in
                               trace_durations_2]
    # Return EMD metric
    return wasserstein_distance(discretized_durations_1, discretized_durations_2)
