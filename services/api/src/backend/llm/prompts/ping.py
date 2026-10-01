from backend.llm.prompts.base import Prompt

PING = Prompt(
    name="ping",
    version="1",
    system="You are a health-check assistant for an API gateway. Reply in one short sentence.",
)
