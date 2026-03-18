import os
import re
from datetime import datetime, timedelta

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
current_schedule = None
current_schedule_ts = None
app = App(token=os.environ["SLACK_BOT_TOKEN"])

# In-memory storage:
# {
#   message_ts: {
#       "title": "Office Hours",
#       "slots": {
#           "Mar 17, 3:00 PM": ["U123"],
#           "Mar 17, 3:30 PM": []
#       }
#   }
# }
signups = {}


def slot_action_id(slot: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", slot.lower())
    return f"pick_slot_{cleaned}"

def format_timestamp_label(ts: int) -> str:
    start = datetime.fromtimestamp(ts)
    end = start + timedelta(hours=2)

    date_part = start.strftime("%b %-d")  # e.g. Mar 17
    start_time = start.strftime("%-I:%M %p").lstrip("0")
    end_time = end.strftime("%-I:%M %p").lstrip("0")

    return f"{date_part}, {start_time}-{end_time}" 

def delete_existing_schedule(client, channel_id: str):
    global current_schedule_ts, current_schedule

    if current_schedule_ts is None:
        return

    try:
        client.chat_delete(channel=channel_id, ts=current_schedule_ts)
    except Exception:
        pass

    current_schedule_ts = None
    current_schedule = None
def build_schedule_blocks(title: str, slots_dict: dict[str, list[str]]) -> list[dict]:
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{title}*"}
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "Click a button to claim a time slot."}
            ]
        }
    ]

    buttons = []
    for slot, users in slots_dict.items():
        buttons.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": f"{slot} ({len(users)})"
                },
                "action_id": slot_action_id(slot),
                "value": slot,
            }
        )

    # Slack action blocks can contain multiple elements; split rows for readability
    for i in range(0, len(buttons), 5):
        blocks.append(
            {
                "type": "actions",
                "elements": buttons[i:i + 5]
            }
        )

   

    signup_lines = []
    for slot, users in slots_dict.items():
        if users:
            mentions = " ".join(f"<@{user_id}>" for user_id in users)
            signup_lines.append(f"*{slot}*: {mentions}")
        else:
            signup_lines.append(f"*{slot}*: _Nobody yet_")

    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Current signups*\n" + "\n".join(signup_lines)
            }
        }
    )

    return blocks


def build_schedule_modal(channel_id: str, num_slots: int, current_values: dict | None = None) -> dict:
    current_values = current_values or {}

    blocks = [
        {
            "type": "input",
            "block_id": "title_block",
            "label": {"type": "plain_text", "text": "Schedule title"},
            "element": {
                "type": "plain_text_input",
                "action_id": "title_input",
                "initial_value": current_values.get("title", "lock in huddle signups"),
            }
        }
    ]

    for i in range(num_slots):
        initial_datetime = current_values.get(f"slot_{i}")
        element = {
            "type": "datetimepicker",
            "action_id": f"slot_input_{i}",
        }
        if initial_datetime is not None:
            element["initial_date_time"] = initial_datetime

        blocks.append(
            {
                "type": "input",
                "block_id": f"slot_block_{i}",
                "label": {"type": "plain_text", "text": f"Time slot {i + 1}"},
                "optional": True,
                "element": element,
            }
        )

    blocks.append(
        {
            "type": "actions",
            "block_id": "add_time_block",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Add another time"},
                    "action_id": "add_time_input",
                    "value": "add_time",
                }
            ]
        }
    )

    return {
        "type": "modal",
        "callback_id": "create_schedule_modal",
        "title": {"type": "plain_text", "text": "New Schedule"},
        "submit": {"type": "plain_text", "text": "Create"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": f"{channel_id}|{num_slots}",
        "blocks": blocks,
    }


@app.command("/schedule")
def handle_schedule_command(ack, body, client):
    ack()

    client.views_open(
        trigger_id=body["trigger_id"],
        view=build_schedule_modal(
            channel_id=body["channel_id"],
            num_slots=2,
        ),
    )
    
    
@app.action("add_time_input")
def handle_add_time_input(ack, body, client):
    ack()

    view = body["view"]
    channel_id, num_slots_str = view["private_metadata"].split("|")
    num_slots = int(num_slots_str)

    values = view.get("state", {}).get("values", {})

    current_values = {}

    title_value = values.get("title_block", {}).get("title_input", {}).get("value")
    if title_value:
        current_values["title"] = title_value

    for i in range(num_slots):
        slot_state = values.get(f"slot_block_{i}", {}).get(f"slot_input_{i}", {})
        selected = slot_state.get("selected_date_time")
        if selected is not None:
            current_values[f"slot_{i}"] = selected

    client.views_update(
        view_id=view["id"],
        hash=view["hash"],
        view=build_schedule_modal(
            channel_id=channel_id,
            num_slots=num_slots + 1,
            current_values=current_values,
        ),
    )

@app.view("create_schedule_modal")
def handle_schedule_modal_submission(ack, body, client, view):
    global current_schedule_ts, current_schedule

    ack()

    values = view["state"]["values"]

    title = values["title_block"]["title_input"]["value"].strip()
    if not title:
        title = "Schedule"

    slot_timestamps = []
    for block_id, block_data in values.items():
        if not block_id.startswith("slot_block_"):
            continue

        action_id = next(iter(block_data))
        selected = block_data[action_id].get("selected_date_time")
        if selected is not None:
            slot_timestamps.append(int(selected))

    slot_timestamps = sorted(set(slot_timestamps))
    if not slot_timestamps:
        return

    slots = {format_timestamp_label(ts): [] for ts in slot_timestamps}

    channel_id = view["private_metadata"].split("|")[0]

    delete_existing_schedule(client, channel_id)

    result = client.chat_postMessage(
        channel=channel_id,
        text=title,
        blocks=build_schedule_blocks(title, slots),
    )

    current_schedule_ts = result["ts"]
    current_schedule = {
        "title": title,
        "slots": slots,
        "channel_id": channel_id,
    }
@app.action(re.compile(r"^pick_slot_"))
def handle_pick_slot(ack, body, client):
    global current_schedule, current_schedule_ts

    ack()

    if current_schedule is None or current_schedule_ts is None:
        return

    user_id = body["user"]["id"]
    chosen_slot = body["actions"][0]["value"]

    slots = current_schedule["slots"]

    if chosen_slot not in slots:
        return

    if user_id in slots[chosen_slot]:
        slots[chosen_slot].remove(user_id)
    else:
        slots[chosen_slot].append(user_id)

    client.chat_update(
        channel=current_schedule["channel_id"],
        ts=current_schedule_ts,
        text=current_schedule["title"],
        blocks=build_schedule_blocks(current_schedule["title"], slots),
    )
@app.action("remove_slot")
def handle_remove_slot(ack, body, client):
    global current_schedule, current_schedule_ts

    ack()

    if current_schedule is None or current_schedule_ts is None:
        return

    user_id = body["user"]["id"]

    for slot_users in current_schedule["slots"].values():
        if user_id in slot_users:
            slot_users.remove(user_id)

    client.chat_update(
        channel=current_schedule["channel_id"],
        ts=current_schedule_ts,
        text=current_schedule["title"],
        blocks=build_schedule_blocks(
            current_schedule["title"],
            current_schedule["slots"],
        ),
    )
@app.command("/add-time-slot")
def handle_add_time_slot_command(ack, body, client):
    ack()

    if current_schedule is None:
        client.chat_postEphemeral(
            channel=body["channel_id"],
            user=body["user_id"],
            text="No active schedule to add to."
        )
        return

    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "add_time_slot_modal",
            "title": {"type": "plain_text", "text": "Add Time Slot"},
            "submit": {"type": "plain_text", "text": "Add"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "slot_block",
                    "label": {"type": "plain_text", "text": "New time slot"},
                    "element": {
                        "type": "datetimepicker",
                        "action_id": "slot_input"
                    }
                }
            ]
        }
    )
@app.view("add_time_slot_modal")
def handle_add_time_slot_submission(ack, body, client, view):
    global current_schedule

    ack()

    if current_schedule is None:
        return

    selected_ts = view["state"]["values"]["slot_block"]["slot_input"]["selected_date_time"]
    slot_label = format_timestamp_label(int(selected_ts))

    if slot_label not in current_schedule["slots"]:
        current_schedule["slots"][slot_label] = []

    # sort slots (simple string sort — works because format is consistent)
    current_schedule["slots"] = dict(sorted(current_schedule["slots"].items()))

    client.chat_update(
        channel=current_schedule["channel_id"],
        ts=current_schedule_ts,
        text=current_schedule["title"],
        blocks=build_schedule_blocks(
            current_schedule["title"],
            current_schedule["slots"]
        ),
    )
    
    
@app.command("/remove-time-slot")
def handle_remove_time_slot_command(ack, body, client):
    ack()

    if current_schedule is None:
        client.chat_postEphemeral(
            channel=body["channel_id"],
            user=body["user_id"],
            text="No active schedule to remove from."
        )
        return

    options = [
        {
            "text": {"type": "plain_text", "text": slot[:75]},
            "value": slot
        }
        for slot in current_schedule["slots"].keys()
    ]

    if not options:
        client.chat_postEphemeral(
            channel=body["channel_id"],
            user=body["user_id"],
            text="No slots to remove."
        )
        return

    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "remove_time_slot_modal",
            "title": {"type": "plain_text", "text": "Remove Time Slot"},
            "submit": {"type": "plain_text", "text": "Remove"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "remove_block",
                    "label": {"type": "plain_text", "text": "Select slot"},
                    "element": {
                        "type": "static_select",
                        "action_id": "remove_input",
                        "options": options
                    }
                }
            ]
        }
    )    
    
    
@app.view("remove_time_slot_modal")
def handle_remove_time_slot_submission(ack, body, client, view):
    global current_schedule

    ack()

    if current_schedule is None:
        return

    selected_slot = view["state"]["values"]["remove_block"]["remove_input"]["selected_option"]["value"]

    if selected_slot in current_schedule["slots"]:
        del current_schedule["slots"][selected_slot]

    client.chat_update(
        channel=current_schedule["channel_id"],
        ts=current_schedule_ts,
        text=current_schedule["title"],
        blocks=build_schedule_blocks(
            current_schedule["title"],
            current_schedule["slots"]
        ),
    )

@app.command("/confirm-schedule")
def finalize_schedule(ack, respond):
    ack()

    if current_schedule is None:
        respond("No active schedule. Make one with /schedule")
        return

    final_str = "*Preview:*\n*Upcoming lock in huddles!*\n\n"

    signup_lines = []
    for slot, users in current_schedule["slots"].items():
        if users:
            mentions = " ".join(f"<@{user_id}>" for user_id in users)
            signup_lines.append(f"*{slot}*: {mentions}")
    if signup_lines == []:
        respond("nobody has signed up for anything! aborting")
        return  

    final_str += "\n".join(signup_lines)

    respond({
        "response_type": "in_channel",
        "text": "Confirm schedule",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": final_str
                }
            },
            {
                "type": "actions",
                "block_id": "confirm_schedule_actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Confirm"},
                        "action_id": "confirm_schedule",
                        "value": "confirm",
                        "style": "primary"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Unconfirm"},
                        "action_id": "unconfirm_schedule",
                        "value": "unconfirm"
                    }
                ]
            }
        ]
    })
    
    
@app.action("confirm_schedule")
def handle_confirm_schedule(ack, body, client):
    global current_schedule, current_schedule_ts
 
    ack()

    if current_schedule is None or current_schedule_ts is None:
        return

    client.chat_postMessage(
        channel=current_schedule["channel_id"],
        text=":white_check_mark: Schedule confirmed."
    )
    current_schedule = None
    current_schedule_ts = None
    
@app.action("unconfirm_schedule")
def handle_unconfirm_schedule(ack, body, client):
    ack()

    if current_schedule is None or current_schedule_ts is None:
        return

    client.chat_postMessage(
        channel=current_schedule["channel_id"],
        text=":x: Schedule unconfirmed."
    )


if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()