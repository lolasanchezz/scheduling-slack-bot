from logging import Logger

from slack_bolt import BoltContext, Say


def sample_message_callback(context: BoltContext, say: Say, logger: Logger):
    try:
        greeting = context["matches"][0]
        user_id = context.get("user_id")
        mention = f"<@{user_id}>" if user_id else "there"
        say(
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"Heysdf {mention}!"},
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Click Me"},
                        "action_id": "button_click",
                    },
                }
            ],
            text=f"{greeting}, how are you?",
        )
    except Exception as e:
        logger.error(e)
