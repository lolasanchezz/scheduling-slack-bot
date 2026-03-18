from slack_bolt import Ack, Respond, Say
from logging import Logger

signups = {}



def make_schedule_callback(command, ack: Ack, say: Say, respond: Respond, logger: Logger):
    try:
        
        ack()
        say("hello")
    except Exception as e:
        logger.error(e)
