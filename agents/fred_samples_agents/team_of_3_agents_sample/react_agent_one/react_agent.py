from fred_sdk import ReActAgent

TEAM3_REACT_AGENT_ONE_ID = "fred.samples.team_of_3.react_math"


class Team3ReActMathAgent(ReActAgent):
    agent_id: str = TEAM3_REACT_AGENT_ONE_ID
    role: str = "Arithmetic and unit conversion helper"
    description: str = (
        "Handles quick arithmetic and simple unit conversions with concise answers."
    )
    tags: tuple[str, ...] = ("sample", "team", "react", "math", "conversion")
    system_prompt_template: str = """\
You are a precise arithmetic and unit-conversion helper.
Always start your answer with this exact marker on its own line:
[ROUTED:REACT_1]

Scope:
- arithmetic (add, subtract, multiply, divide, percentages)
- simple metric conversions (mm/cm/m/km and g/kg)

If a request is outside this scope, say it briefly and still include the marker.
"""


TEAM3_REACT_AGENT_ONE = Team3ReActMathAgent()

