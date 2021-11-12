import json
import xml.etree.ElementTree as ET

from bpdfr_simulation_engine.control_flow_manager import BPMNGraph, ElementInfo, BPMN
from bpdfr_simulation_engine.resource_calendar import RCalendar, convert_time_unit_from_to, convertion_table, to_seconds
from bpdfr_simulation_engine.resource_profile import ResourceProfile, PoolInfo
from bpdfr_simulation_engine.probability_distributions import *

bpmn_schema_url = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
simod_ns = {'qbp': 'http://www.qbp-simulator.com/Schema201212'}
bpmn_element_ns = {'xmlns': bpmn_schema_url}


def parse_json_sim_parameters(json_path):
    with open(json_path) as json_file:
        json_data = json.load(json_file)
        resources_map, calendars_map = parse_calendar_from_json(json_data["resource_calendars"])
        task_resource_distribution = json_data["task_resource_distribution"]
        element_distribution = parse_simulation_parameters(json_data["arrival_time_distribution"],
                                                           json_data["gateway_branching_probabilities"])
        arrival_calendar = parse_arrival_calendar(json_data)
        parse_pool_info(json_data["resource_profiles"], resources_map)

        return resources_map, calendars_map, element_distribution, task_resource_distribution, arrival_calendar


def parse_pool_info(json_data, resources_map):
    for pool_id in json_data:
        pool_name = json_data[pool_id]["name"]
        for res_info in json_data[pool_id]["resource_list"]:
            r_id = res_info["id"]
            resources_map[r_id].pool_info = PoolInfo(pool_id, pool_name)
            resources_map[r_id].resource_name = res_info["name"]
            resources_map[r_id].cost_per_hour = float(res_info["cost_per_hour"])
            resources_map[r_id].resource_amount = int(res_info["amount"])


def parse_arrival_calendar(json_data):
    arrival_calendar = None
    if 'arrival_time_calendar' in json_data:
        arrival_calendar = RCalendar('arrival_time_calendar')
        for c_item in json_data['arrival_time_calendar']:
            arrival_calendar.add_calendar_item(c_item['from'], c_item['to'], c_item['beginTime'], c_item['endTime'])
    return arrival_calendar


def parse_calendar_from_json(json_data):
    resources_map = dict()
    calendars_map = dict()
    for r_id in json_data:
        calendar_id = "%s_timetable" % r_id
        resources_map[r_id] = ResourceProfile(r_id, r_id, calendar_id, 1.0)
        r_calendar = RCalendar(calendar_id)
        for c_item in json_data[r_id]:
            r_calendar.add_calendar_item(c_item['from'], c_item['to'], c_item['beginTime'], c_item['endTime'])
        r_calendar.compute_cumulative_durations()
        calendars_map[r_calendar.calendar_id] = r_calendar
    return resources_map, calendars_map


def parse_simulation_parameters(arrival_json, gateway_json):
    element_distribution = dict()

    element_distribution['arrivalTime'] = arrival_json

    for gateway_id in gateway_json:
        probability_list = list()
        out_arc = list()
        for flow_arc in gateway_json[gateway_id]:
            out_arc.append(flow_arc)
            probability_list.append(gateway_json[gateway_id][flow_arc])
        element_distribution[gateway_id] = Choice(out_arc, probability_list)

    return element_distribution


def parse_simulation_model(bpmn_path):
    tree = ET.parse(bpmn_path)
    root = tree.getroot()

    to_extract = {'xmlns:task': BPMN.TASK,
                  'xmlns:startEvent': BPMN.START_EVENT,
                  'xmlns:endEvent': BPMN.END_EVENT,
                  'xmlns:exclusiveGateway': BPMN.EXCLUSIVE_GATEWAY,
                  'xmlns:parallelGateway': BPMN.PARALLEL_GATEWAY,
                  'xmlns:inclusiveGateway': BPMN.INCLUSIVE_GATEWAY}

    bpmn_graph = BPMNGraph()
    for process in root.findall('xmlns:process', bpmn_element_ns):
        for xmlns_key in to_extract:
            for bpmn_element in process.findall(xmlns_key, bpmn_element_ns):
                name = bpmn_element.attrib["name"] \
                    if "name" in bpmn_element.attrib and len(bpmn_element.attrib["name"]) > 0 \
                    else bpmn_element.attrib["id"]
                bpmn_graph.add_bpmn_element(bpmn_element.attrib["id"],
                                            ElementInfo(to_extract[xmlns_key], bpmn_element.attrib["id"], name))
        for flow_arc in process.findall('xmlns:sequenceFlow', bpmn_element_ns):
            bpmn_graph.add_flow_arc(flow_arc.attrib["id"], flow_arc.attrib["sourceRef"], flow_arc.attrib["targetRef"])
    bpmn_graph.encode_or_join_predecesors()
    return bpmn_graph


def parse_qbp_simulation_process(qbp_bpmn_path, out_file):
    tree = ET.parse(qbp_bpmn_path)
    root = tree.getroot()
    simod_root = root.find("qbp:processSimulationInfo", simod_ns)

    # 1. Extracting gateway branching probabilities
    gateways_branching = dict()
    reverse_map = dict()
    for process in root.findall('xmlns:process', bpmn_element_ns):
        for xmlns_key in ['xmlns:exclusiveGateway', 'xmlns:inclusiveGateway']:
            for bpmn_element in process.findall(xmlns_key, bpmn_element_ns):
                if bpmn_element.attrib["gatewayDirection"] == "Diverging":
                    gateways_branching[bpmn_element.attrib["id"]] = dict()
                    for out_flow in bpmn_element.findall("xmlns:outgoing", bpmn_element_ns):
                        arc_id = out_flow.text.strip()
                        gateways_branching[bpmn_element.attrib["id"]][arc_id] = 0
                        reverse_map[arc_id] = bpmn_element.attrib["id"]
    for flow_prob in simod_root.find("qbp:sequenceFlows", simod_ns).findall("qbp:sequenceFlow", simod_ns):
        flow_id = flow_prob.attrib["elementId"]
        gateways_branching[reverse_map[flow_id]][flow_id] = flow_prob.attrib["executionProbability"]

    # 2. Extracting Resource Calendars
    resource_pools = dict()
    calendars_map = dict()
    bpmn_calendars = simod_root.find("qbp:timetables", simod_ns)
    arrival_calendar_id = None

    for calendar_info in bpmn_calendars:
        calendar_id = calendar_info.attrib["id"]
        if calendar_id not in calendars_map:
            calendars_map[calendar_id] = list()

        time_tables = calendar_info.find("qbp:rules", simod_ns).findall("qbp:rule", simod_ns)
        if 'ARRIVAL_CALENDAR' in calendar_id or (arrival_calendar_id is None and 'DEFAULT_TIMETABLE' in calendar_id):
            arrival_calendar_id = calendar_id
        for time_table in time_tables:
            calendars_map[calendar_id].append({"from": time_table.attrib["fromWeekDay"],
                                               "to": time_table.attrib["toWeekDay"],
                                               "beginTime": format_date(time_table.attrib["fromTime"]),
                                               "endTime": format_date(time_table.attrib["toTime"])})

    # 3. Extracting Arrival time distribution
    arrival_time_dist = extract_dist_params(simod_root.find("qbp:arrivalRateDistribution", simod_ns))

    # 4. Extracting task-resource duration distributions
    bpmn_resources = simod_root.find("qbp:resources", simod_ns)
    simod_elements = simod_root.find("qbp:elements", simod_ns)
    pools_json = dict()

    resource_calendars = dict()
    for resource in bpmn_resources:
        pools_json[resource.attrib["id"]] = {"name": resource.attrib["name"], "resource_list": list()}
        resource_pools[resource.attrib["id"]] = list()
        calendar_id = resource.attrib["timetableId"]
        for i in range(1, int(resource.attrib["totalAmount"]) + 1):
            nr_id = "%s_%d" % (resource.attrib["id"], i)
            pools_json[resource.attrib["id"]]["resource_list"].append({
                "id": nr_id,
                "name": "%s_%d" % (resource.attrib["name"], i),
                "cost_per_hour": resource.attrib["costPerHour"],
                "amount": 1
            })
            resource_pools[resource.attrib["id"]].append(nr_id)
            resource_calendars[nr_id] = calendars_map[calendar_id]

    task_resource_dist = dict()
    for e_inf in simod_elements:
        task_id = e_inf.attrib["elementId"]
        rpool_id = e_inf.find("qbp:resourceIds", simod_ns).find("qbp:resourceId", simod_ns).text
        dist_info = e_inf.find("qbp:durationDistribution", simod_ns)

        t_dist = extract_dist_params(dist_info)
        if task_id not in task_resource_dist:
            task_resource_dist[task_id] = dict()
        for rp_id in resource_pools[rpool_id]:
            task_resource_dist[task_id][rp_id] = t_dist

    # 5.Saving all in a single JSON file

    to_save = {
        "resource_profiles": pools_json,
        "arrival_time_distribution": arrival_time_dist,
        "arrival_time_calendar": calendars_map[arrival_calendar_id],
        "gateway_branching_probabilities": gateways_branching,
        "task_resource_distribution": task_resource_dist,
        "resource_calendars": resource_calendars,
    }
    with open(out_file, 'w') as file_writter:
        json.dump(to_save, file_writter)


def extract_dist_params(dist_info):
    # time_unit = dist_info.find("qbp:timeUnit", simod_ns).text
    # The time_tables produced by bimp always have the parameters in seconds, although it shouws other time units in
    # the XML file.
    dist_params = {"mean": float(dist_info.attrib["mean"]),
                   "arg1": float(dist_info.attrib["arg1"]),
                   "arg2": float(dist_info.attrib["arg2"])}
    dist_name = dist_info.attrib["type"].upper()
    if dist_name == "EXPONENTIAL":
        # input: loc = 0, scale = mean
        return {"distribution_name": "expon", "distribution_params": [0, dist_params["arg1"]]}
    if dist_name == "NORMAL":
        # input: loc = mean, scale = standard deviation
        return {"distribution_name": "norm", "distribution_params": [dist_params["mean"], dist_params["arg1"]]}
    if dist_name == "FIXED":
        return {"distribution_name": "fix", "distribution_params": [dist_params["mean"], 0, 1]}
    # if dist_name == "LOGNORMAL":
    #     # input: shape = standard deviation, loc = 0, scale exp(mean)
    #     return {"distribution_name": "lognorm", "distribution_params": [dist_params["mean"],
    #                                                                     dist_params["arg1"], 0, 1]}
    if dist_name == "UNIFORM":
        # input: loc = from, scale = to - from
        return {"distribution_name": "uniform", "distribution_params": [dist_params["arg1"],
                                                                        dist_params["arg2"] - dist_params["arg2"]]}
    if dist_name == "GAMMA":
        # input: shape, loc=0, scale
        mean, variance = dist_params["mean"], dist_params["arg1"]
        return {"distribution_name": "gamma", "distribution_params": [pow(mean, 2) / variance, 0, variance / mean]}
    if dist_name == "TRIANGULAR":
        # input: c = mode, loc = min, scale = max - min
        return {"distribution_name": "triang", "distribution_params": [dist_params["mean"], dist_params["arg1"],
                                                                       dist_params["arg2"] - dist_params["arg1"]]}
    return None


def format_date(date_str):
    date_splt = date_str.split("+")
    if len(date_splt) == 2 and date_splt[1] == "00:00":
        return date_splt[0]
    return date_str