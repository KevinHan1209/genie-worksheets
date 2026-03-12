from enum import Enum

from worksheets.core.fields import GenieField
from worksheets.core.worksheet import Action, GenieWorksheet


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Disposition(str, Enum):
    qualified = "qualified"
    qualified_with_concerns = "qualified_with_concerns"
    not_viable = "not_viable"


# ---------------------------------------------------------------------------
# Sub-worksheets (collected by Main)
# ---------------------------------------------------------------------------

class LeadContact(GenieWorksheet):
    predicate = "main is not None"
    actions = Action("@sync_lead_contact(self, main.lead_id)")
    backend_api = ""
    outputs = []
    """Phase 1: confirm identity and get permission to continue."""

    is_right_person = GenieField(
        bool,
        "is_right_person",
        description="Whether we reached the person who submitted the quote request",
    )
    identity_confirmed = GenieField(
        bool,
        "identity_confirmed",
        description="The person confirmed they are the quote requester",
        predicate="self.is_right_person == True",
    )
    permission_to_continue = GenieField(
        bool,
        "permission_to_continue",
        description=(
            "Say: 'I'm not a licensed insurance agent, but I would like to verify a "
            "few details from your quote and see if you'd like to schedule a call with "
            "a licensed agent. Do you have a few minutes?' and capture whether they agree"
        ),
        predicate="self.identity_confirmed == True",
    )
    callback_requested = GenieField(
        bool,
        "callback_requested",
        description="The person requests a callback at a different time",
        predicate="self.identity_confirmed == True and self.permission_to_continue == False",
    )
    callback_time_text = GenieField(
        str,
        "callback_time_text",
        description="Preferred time for the callback (e.g. 'this afternoon', 'tomorrow morning')",
        predicate="self.callback_requested == True",
        optional=True,
    )
    do_not_call = GenieField(
        bool,
        "do_not_call",
        description="The person requests to be placed on the do-not-call list",
        predicate="self.identity_confirmed == True and self.permission_to_continue == False and not self.callback_requested == True",
    )
    wrong_person_notes = GenieField(
        str,
        "wrong_person_notes",
        description="Notes about the wrong-person contact (e.g. who answered, any forwarding info)",
        predicate="not self.is_right_person == True",
        optional=True,
    )


class VerifyQuote(GenieWorksheet):
    predicate = "main is not None and main.lead_contact.permission_to_continue == True"
    actions = Action("@sync_verify_quote(self, main.lead_id)")
    backend_api = ""
    outputs = []
    """Phase 2: read back on-file quote details and capture corrections."""

    property_address = GenieField(
        str,
        "property_address",
        description="What is the property address for this quote?",
        requires_confirmation=True,
    )
    property_type = GenieField(
        str,
        "property_type",
        description="What is the property type for this quote (for example, single-family home, condo, townhouse)?",
        requires_confirmation=True,
    )
    coverage_start = GenieField(
        str,
        "coverage_start",
        description="What is the requested coverage start date for this quote?",
        requires_confirmation=True,
    )
    target_name = GenieField(
        str,
        "target_name",
        description="Lead name associated with the quote record",
        ask=False,
        internal=True,
        optional=True,
    )

class UnderwritingIntake(GenieWorksheet):
    predicate = (
        "main is not None and "
        "main.verify_quote is not None and "
        "main.verify_quote.property_address.confirmed == True and "
        "main.verify_quote.property_type.confirmed == True and "
        "main.verify_quote.coverage_start.confirmed == True"
    )
    actions = Action("@sync_underwriting_intake(self, main.lead_id)")
    backend_api = ""
    outputs = []
    """Phase 3: collect basic underwriting qualification data."""

    roof_age_years = GenieField(
        str,
        "roof_age_years",
        description="Do you happen to know the approximate age of your roof?",
        requires_confirmation=True,
    )
    claims_past_five_years = GenieField(
        str,
        "claims_past_five_years",
        description="Have there been any insurance claims on the property in the past five years?",
        requires_confirmation=True,
    )
    claims_details = GenieField(
        str,
        "claims_details",
        description="Brief description of past claims (type, approximate amount)",
        predicate="self.claims_past_five_years == 'one' or self.claims_past_five_years == 'multiple'",
        requires_confirmation=True,
    )
    notes = GenieField(
        str,
        "notes",
        description="Any additional notes from the underwriting conversation",
        ask=False,
        optional=True,
    )
    disposition = GenieField(
        Disposition,
        "disposition",
        description="Underwriting disposition computed from intake data",
        ask=False,
        internal=True,
        optional=True,
    )
class ScheduleConsultation(GenieWorksheet):
    predicate = (
        "main is not None and "
        "main.underwriting_intake is not None and "
        "main.underwriting_intake.roof_age_years.confirmed == True and "
        "main.underwriting_intake.claims_past_five_years.confirmed == True and "
        "("
        "main.underwriting_intake.claims_past_five_years != 'one' and "
        "main.underwriting_intake.claims_past_five_years != 'multiple'"
        " or main.underwriting_intake.claims_details.confirmed == True"
        ")"
    )
    actions = Action("@sync_schedule_consultation(self, main.lead_id)")
    backend_api = ""
    outputs = []
    """Phase 4: offer and book a consultation slot with a licensed agent."""

    interested_in_consultation = GenieField(
        bool,
        "interested_in_consultation",
        description="Ask whether they are interested in scheduling a phone consultation with a licensed agent",
    )
    time_preference = GenieField(
        str,
        "time_preference",
        description="Do you have a preference for morning or afternoon for the consultation?",
        predicate="self.interested_in_consultation == True",
        optional=True,
    )
    selected_slot = GenieField(
        str,
        "selected_slot",
        description="I can offer available consultation slots. Which time works best for you?",
        predicate="self.interested_in_consultation == True",
    )
    booking_confirmed = GenieField(
        bool,
        "booking_confirmed",
        description="Should I go ahead and book that consultation slot for you?",
        predicate="self.interested_in_consultation == True",
    )


# ---------------------------------------------------------------------------
# Main orchestrating worksheet
# ---------------------------------------------------------------------------

class Main(GenieWorksheet):
    predicate = ""
    actions = Action("")
    backend_api = ""
    outputs = []
    """Top-level worksheet orchestrating the outbound lead follow-up call.

    The sequential flow is:
    LeadContact -> VerifyQuote -> UnderwritingIntake -> ScheduleConsultation
    with early-exit branches for wrong-person, refusal, callback, and DNC.
    """

    lead_id = GenieField(
        str,
        "lead_id",
        description="Internal lead identifier",
        internal=True,
        optional=True,
    )
    target_name = GenieField(
        str,
        "target_name",
        description="Name of the person we are calling",
        internal=True,
        optional=True,
    )
    lead_contact = GenieField(
        LeadContact,
        "lead_contact",
        description=(
            "Confirm you reached the right person and whether they have a few "
            "minutes to review quote details before continuing"
        ),
    )
    call_should_proceed = GenieField(
        bool,
        "call_should_proceed",
        description="Whether the lead contact phase determined we should proceed",
        internal=True,
        ask=False,
        optional=True,
    )
    verify_quote = GenieField(
        VerifyQuote,
        "verify_quote",
        description="Can I quickly verify the quote details I have on file with you?",
        predicate="self.lead_contact.permission_to_continue == True",
        actions=Action("@hydrate_verify_quote(self.verify_quote, self.lead_id)"),
    )
    verification_done = GenieField(
        bool,
        "verification_done",
        description="Whether quote verification is complete",
        internal=True,
        ask=False,
        optional=True,
    )
    underwriting_intake = GenieField(
        UnderwritingIntake,
        "underwriting_intake",
        description="Great, to continue, about how old is your roof?",
        predicate=(
            "self.verify_quote is not None and "
            "self.verify_quote.property_address.confirmed == True and "
            "self.verify_quote.property_type.confirmed == True and "
            "self.verify_quote.coverage_start.confirmed == True"
        ),
    )
    intake_done = GenieField(
        bool,
        "intake_done",
        description="Whether underwriting intake required fields are complete and confirmed",
        internal=True,
        ask=False,
        optional=True,
    )
    schedule_consultation = GenieField(
        ScheduleConsultation,
        "schedule_consultation",
        description="Would you like to schedule a quick phone consultation with a licensed agent?",
        predicate=(
            "self.underwriting_intake is not None and "
            "self.underwriting_intake.roof_age_years.confirmed == True and "
            "self.underwriting_intake.claims_past_five_years.confirmed == True and "
            "("
            "self.underwriting_intake.claims_past_five_years != 'one' and "
            "self.underwriting_intake.claims_past_five_years != 'multiple'"
            " or self.underwriting_intake.claims_details.confirmed == True"
            ")"
        ),
    )
    finalization_done = GenieField(
        bool,
        "finalization_done",
        description="Whether terminal outcome has been persisted",
        internal=True,
        ask=False,
        optional=True,
    )
    final_confirm = GenieField(
        bool,
        "final_confirm",
        description=(
            "Are there any other details you'd like to add or change? "
            "If so, please let me know. Otherwise I'll wrap things up."
        ),
        predicate=(
            "self.lead_contact is not None and "
            "self.schedule_consultation is not None and "
            "("
            "self.schedule_consultation.interested_in_consultation == False or "
            "self.schedule_consultation.booking_confirmed is not None"
            ")"
        ),
        actions=Action(
            "if self.final_confirm == True:\n"
            "    @finalize_confirmed_main(main)\n"
            "    say('All details have been recorded and your information has been submitted. Thank you!')\n"
            "if self.final_confirm == False:\n"
            "    self.final_confirm = None"
        ),
    )
