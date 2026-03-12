import os

from insurance_lead_followup.api import (
    book_appointment,
    check_availability,
    process_lead_outcome,
)

current_dir = os.path.dirname(os.path.abspath(__file__))
prompt_dir = os.path.join(current_dir, "prompts")

botname = "InsuranceLeadBot"
description = (
    "You are an outbound follow-up assistant for Prestige Home Insurance. "
    "You are not a licensed insurance agent. Confirm identity, verify quote "
    "details, collect underwriting information, and schedule consultations "
    "with a licensed agent."
)
starting_prompt = (
    "Hello, this is an automated assistant calling on behalf of Prestige Home "
    "Insurance. Am I speaking with Kevin?"
)
api_list = [process_lead_outcome, check_availability, book_appointment]
