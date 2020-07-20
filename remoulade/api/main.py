""" This file describe the API to get the state of messages """
import datetime
from collections import defaultdict
from operator import itemgetter

from flask import Flask, request
from marshmallow import ValidationError
from werkzeug.exceptions import HTTPException, NotFound

import remoulade
from remoulade import get_broker, get_scheduler
from remoulade.errors import NoScheduler, RemouladeError

from .schema import MessageSchema, PageSchema

app = Flask(__name__)


def sort_dicts(data, column, reverse=False):
    """ Sort an array of dicts by a given column """
    data_none = [item for item in data if item.get(column) is None]
    data = sorted((item for item in data if item.get(column)), key=itemgetter(column), reverse=reverse)
    data.extend(data_none)
    return data


def dict_has(item, keys, value):
    """ Check if the value of some key in keys has a value"""
    return chr(0).join([str(item[k]) for k in keys if item.get(k)]).lower().find(value) >= 0


@app.route("/messages/states")
def get_states():
    args = PageSchema().load(request.args.to_dict())
    backend = remoulade.get_broker().get_state_backend()
    data = [s.as_dict(encode_args=True) for s in backend.get_states()]
    if args.get("search_value"):
        keys = ["message_id", "name", "actor_name", "args", "kwargs"]
        value = args["search_value"].lower()
        data = [item for item in data if dict_has(item, keys, value)]

    if args.get("sort_column"):
        reverse = args.get("sort_direction") == "desc"
        sort_column = args["sort_column"]
        data = sort_dicts(data, sort_column, reverse)

    return {"data": data[args["offset"] : args["size"] + args["offset"]], "count": len(data)}


@app.route("/messages/state/<message_id>")
def get_state(message_id):
    backend = remoulade.get_broker().get_state_backend()
    data = backend.get_state(message_id)
    if data is None:
        raise NotFound("message_id = {} does not exist".format(message_id))
    return data.as_dict(encode_args=True)


@app.route("/messages/cancel/<message_id>", methods=["POST"])
def cancel_message(message_id):
    backend = remoulade.get_broker().get_cancel_backend()
    backend.cancel([message_id])
    return {"result": True}


@app.route("/scheduled/jobs")
def get_scheduled_jobs():
    try:
        scheduler = get_scheduler()
    except NoScheduler:
        return {"result": []}
    scheduled_jobs = scheduler.get_redis_schedule()
    return {"result": [job.as_dict() for job in scheduled_jobs.values()]}


@app.route("/messages", methods=["POST"])
def enqueue_message():
    payload = MessageSchema().load(request.json)
    actor = get_broker().get_actor(payload.pop("actor_name"))
    options = payload.pop("options") or {}
    actor.send_with_options(**payload, **options)
    return {"result": "ok"}


@app.route("/actors")
def get_actors():
    return {"result": [actor.as_dict() for actor in get_broker().actors.values()]}


@app.route("/groups")
def get_groups():
    args = PageSchema().load(request.args.to_dict())
    backend = remoulade.get_broker().get_state_backend()
    groups = defaultdict(list)
    states = (state for state in backend.get_states() if state.group_id)

    if args.get("search_value"):
        keys = ["message_id", "name", "actor_name", "group_id"]
        value = args["search_value"].lower()
        states = [state for state in states if dict_has(state.as_dict(), keys, value)]  # type: ignore

    for state in states:
        groups[state.group_id].append(state.as_dict(exclude_keys=("args", "kwargs")))

    groups = sorted(  # type: ignore
        ({"group_id": group_id, "messages": messages} for group_id, messages in groups.items()),
        key=lambda x: x["messages"][0].get("enqueued_datetime") or datetime.datetime.min,
        reverse=True,
    )
    return {"data": groups[args["offset"] : args["size"] + args["offset"]], "count": len(groups)}


@app.errorhandler(RemouladeError)
def remoulade_exception(e):
    return {"error": str(e)}, 500


@app.errorhandler(HTTPException)
def http_exception(e):
    return {"error": str(e)}, e.code


@app.errorhandler(ValidationError)
def validation_error(e):
    return {"error": e.normalized_messages()}, 400
