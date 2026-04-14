from pydantic import BaseModel


class Team3GraphInput(BaseModel):
    message: str


class Team3GraphState(BaseModel):
    latest_user_text: str = ""
    decision: str = ""
    reason: str = ""
    final_text: str = ""
