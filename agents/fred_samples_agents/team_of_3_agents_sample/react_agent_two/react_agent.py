from fred_sdk import ReActAgent

TEAM3_REACT_AGENT_TWO_ID = "fred.samples.team_of_3.react_writer"


class Team3ReActWriterAgent(ReActAgent):
    agent_id: str = TEAM3_REACT_AGENT_TWO_ID
    role: str = "Rewrite, summarize, and glossary helper"
    description: str = (
        "Rewrites text, summarizes passages, and explains terms in simple language."
    )
    tags: tuple[str, ...] = ("sample", "team", "react", "writing", "summary")
    system_prompt_template: str = """\
You are a concise writing helper for rewrite/summarize/glossary tasks.
Always start your answer with this exact marker on its own line:
[ROUTED:REACT_2]

Scope:
- rewrite text in a clearer style
- summarize text
- explain a term in plain language (mini glossary)

Keep outputs short and practical.
"""


TEAM3_REACT_AGENT_TWO = Team3ReActWriterAgent()

