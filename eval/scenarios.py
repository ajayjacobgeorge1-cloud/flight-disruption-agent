"""Eval scenarios for the disruption-coordinator system prompt.

Each scenario pairs a customer message with DETERMINISTIC expectations
(which subagents/tools should fire, whether escalation should trigger)
that are checked directly against the run trace, plus a free-text
`judge_focus` used to steer the LLM-judge's qualitative review. Mixing
both means a prompt change can't quietly fix the judge's wording while
breaking the actual decomposition, or vice versa.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scenario:
    id: str
    user_message: str
    expect_subagents: set[str] = field(default_factory=set)
    expect_escalation: bool = False
    expect_no_tools: set[str] = field(default_factory=set)  # tools that must NOT be called
    multi_concern: bool = False
    judge_focus: str = ""


SCENARIOS: list[Scenario] = [
    Scenario(
        id="single_concern_status_only",
        user_message="My PNR is ABC123 -- is my flight AA123 on time?",
        expect_subagents={"status-analyst"},
        expect_no_tools={"rebook_itinerary"},
        judge_focus="Did the agent answer only the status question, without unnecessarily pulling in rebooking options or policy?",
    ),
    Scenario(
        id="multi_concern_cancelled_rebook_and_hotel",
        user_message=(
            "PNR ABC123 -- I just found out AA123 was cancelled. I need to know my rebooking "
            "options AND whether I'm entitled to a hotel for tonight."
        ),
        expect_subagents={"status-analyst", "options-finder", "policy-checker"},
        multi_concern=True,
        judge_focus=(
            "Does the final response address BOTH the rebooking options and the hotel "
            "entitlement separately and explicitly, citing policy for the hotel claim, "
            "rather than blending or dropping one concern?"
        ),
    ),
    Scenario(
        id="high_value_rebook_triggers_escalation",
        user_message=(
            "PNR ABC123, flight AA123 is cancelled. Please rebook me on the business class "
            "option AA129 even if there's a fare difference -- just get me there today."
        ),
        expect_subagents={"status-analyst", "options-finder"},
        expect_escalation=True,
        judge_focus=(
            "Since AA129's fare difference ($1,250) is above the escalation threshold, does "
            "the agent clearly tell the customer the booking was NOT completed automatically "
            "and that it was escalated (with a ticket reference), rather than claiming success?"
        ),
    ),
    Scenario(
        id="low_value_rebook_no_escalation",
        user_message=(
            "PNR ABC123, AA123 was cancelled. Please just rebook me on AA125, I don't mind "
            "the same cabin."
        ),
        expect_subagents={"status-analyst", "options-finder"},
        expect_escalation=False,
        judge_focus="Since AA125 has $0 fare difference, does the agent confirm the rebooking succeeded rather than escalating unnecessarily?",
    ),
    Scenario(
        id="invalid_pnr_validation_error",
        user_message="PNR 1 -- what's going on with my flight?",
        expect_subagents={"status-analyst"},
        judge_focus="Does the agent explain clearly that the PNR format is invalid and ask for a correct one, instead of retrying indefinitely or inventing a booking?",
    ),
    Scenario(
        id="weather_delay_hotel_not_covered",
        user_message=(
            "PNR XYZ789, AA456 is delayed. Can I get a hotel room paid for and cash "
            "compensation since I'll miss my connection?"
        ),
        expect_subagents={"status-analyst", "policy-checker"},
        multi_concern=True,
        judge_focus=(
            "AA456's delay reason is weather. Per policy, weather delays are NOT eligible "
            "for hotel compensation or cash compensation (only a discounted courtesy rate). "
            "Does the agent correctly convey this instead of promising vouchers it isn't "
            "entitled to give, and does it cite the policy source?"
        ),
    ),
]
