import asyncio
import json
import time
from typing import Tuple, List, Dict

from gmqtt import Client as MQTTClient

from toad_sp_controller import config, etcdclient, logger, smartplug
from toad_sp_controller.config import MQTT_TOPIC, COLUMNS_POR_ROW, ROWS_PER_COLUMN

STOP = asyncio.Event()


cached_ips: Dict[str, str] = {}


def on_connect(client, flags, rc, properties):
    print("Connected")
    client.subscribe("TEST/#", qos=0)


def on_message(client, topic, payload, qos, properties):
    print(f"RECV MSG with topic: '{topic}', payload: '{payload}'")
    targets, status, err = parse_message(topic, payload, cached_ips)
    logger.log_error_verbose(f"Targets: [{', '.join(targets)}]")
    success, failed = [], []
    for target in targets:
        ok, _ = smartplug.set_status(status, target)
        if ok:
            success.append(target)
        else:
            failed.append(target)
    logger.log_info_verbose(f"Command OK for targets: [{', '.join(success)}]")
    logger.log_info_verbose(f"Command ERR for targets: [{', '.join(failed)}]")


def on_disconnect(client, packet, exc=None):
    print("Disconnected")


def on_subscribe(client, mid, qos, properties):
    print("SUBSCRIBED")


def parse_message(
    topic: str, payload: str, ips: Dict[str, str]
) -> Tuple[List[str], bool, str]:
    """
    Parse a message received via MQTT.

    :param topic: MQTT topic
    :param payload: MQTT paylaod
    :param ips: ID->IP map
    :return: targets, status, error
    """
    if not topic.startswith(MQTT_TOPIC):
        return [], False, f"Invalid topic: {topic}, it should start with {MQTT_TOPIC}"
    # remove MQTT_TOPIC from the topic to get the query
    n = len(MQTT_TOPIC) - 1
    query = topic[n:]
    if query != "" and query[0] == "/":
        query = query[1:]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as err:
        return [], False, err.__str__()

    data_payload = data.get("payload")
    if data_payload is None:
        return [], False, f"Missing payload, check the documentation for more info"
    try:
        data_payload = json.loads(data_payload)
    except json.JSONDecodeError as err:
        return [], False, err.__str__()

    status = False
    # this assignation is not yet supported by flake8 so it nees to be two steps
    # if status_value := data_payload.get("status"):
    status_value = data_payload.get("status")
    if status_value:
        try:
            status_value = int(status_value)
            if status_value != 1 and status_value != 0:
                raise ValueError
            status = status_value == 1
        except ValueError:
            return [], False, f"Invalid payload status: '{status_value}'"

    subtopics = data.get("subtopics")
    if subtopics is None and len(query) < 1:
        return [], False, "No targets specified"

    topics = []
    if subtopics is None:
        topics.append(query)
    else:
        topics = [f"{query}{st}" for st in subtopics]

    targets = []
    for topic in topics:
        if "row" in topic:
            row = topic.split("/")[-1]
            for sp in [f"w.r{row}.c{x}" for x in range(COLUMNS_POR_ROW)]:
                target = ips.get(sp)
                if target:
                    targets.append(target)
        elif "column" in topic:
            column = topic.split("/")[-1]
            for sp in [f"w.r{x}.c{column}" for x in range(ROWS_PER_COLUMN)]:
                target = ips.get(sp)
                if target:
                    targets.append(target)
        else:
            target = ips.get(topic)
            if target:
                targets.append(target)
    return targets, status, ""


def ask_exit(*args):
    STOP.set()


async def main(broker_host):

    global cached_ips

    client = MQTTClient("client-id")

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.on_subscribe = on_subscribe

    await client.connect(broker_host)

    client.publish("TEST/TIME", str(time.time()), qos=1)

    while True:
        if STOP.is_set():
            await client.disconnect()
            exit(0)
        cached_ips = etcdclient.get_cached_ips(
            config.ETCD_HOST, config.ETCD_PORT, config.ETCD_KEY
        )
        await asyncio.sleep(config.SLEEP_TIME)