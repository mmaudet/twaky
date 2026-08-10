"""Prompt de classification d'état de fil (4-way classifier).

Porte le prompt le plus affiné du pipeline. Sa valeur tient entièrement au
traitement des cas limites listés dans « CRITICAL RULES ».

Technique conservée : lorsque l'utilisateur a envoyé le dernier message,
l'option ``FYI`` est retirée de l'énumération au lieu d'être interdite par
instruction — le modèle ne voit jamais l'option invalide.
"""

from __future__ import annotations

from typing import Any

from twaky.sentinels.mail.prompts.helpers import (
    email_list_block,
    today_for_llm,
    user_info_block,
)


def thread_status_prompt(
    state: dict[str, Any],
    *,
    owner_email: str = "",
) -> str:
    """4-way email thread status classifier.

    Statuses: TO_REPLY, ACTIONED, FYI, AWAITING_REPLY.
    Edge case: if a trusted delegate wrote "je m'en occupe" (or equivalent),
    treat the original request as ACTIONED.
    """
    thread: list[dict[str, Any]] = state.get("thread", [])

    # Determine if the owner sent the last email (FYI suppression technique)
    user_sent_last_email = False
    if thread and owner_email:
        last = thread[-1]
        last_from = str(last.get("from", "")).lower()
        user_sent_last_email = owner_email.lower() in last_from

    fyi_option = "" if user_sent_last_email else "\n* FYI - No reply needed"

    fyi_criteria = (
        ""
        if user_sent_last_email
        else """

**FYI**: Information the user RECEIVED that they should be aware of, but doesn't require a response. Use this when:
- Someone sent the user important updates, announcements, or information they should know about
- The user is CC'd on important matters for their awareness only
- Someone sent status updates that are valuable to know but don't need acknowledgment
- Someone provided requested information/instructions and now the ball is in the user's court to optionally act on it
- NO questions or requests exist anywhere in the thread
- CRITICAL: FYI is ONLY for emails the user RECEIVED. If the user SENT the last email, it cannot be FYI."""
    )

    rule_5_tail = (
        "ACTIONED (if fully resolved)"
        if user_sent_last_email
        else "FYI (if informational) or ACTIONED (if fully resolved)"
    )

    rule_10 = (
        """
10. **User sent last email**: Since the user sent the last email, FYI is NOT an option. Choose AWAITING_REPLY if waiting for a response, or ACTIONED if the thread is complete."""
        if user_sent_last_email
        else """
10. **FYI is only when nothing is pending**: Use FYI ONLY when there are absolutely no questions, requests, or pending actions in the entire thread"""
    )

    status_enum = (
        "TO_REPLY, AWAITING_REPLY, or ACTIONED"
        if user_sent_last_email
        else "TO_REPLY, AWAITING_REPLY, FYI, or ACTIONED"
    )

    thread_block = email_list_block(thread)

    return f"""You are an AI assistant that analyzes email threads to determine their current status.

{user_info_block(owner_email)}

Today: {today_for_llm()}

Your task is to determine the current status of an email thread from the user's perspective. The thread can be in ONE of these mutually exclusive states:

* TO_REPLY - We need to reply
* AWAITING_REPLY - We're waiting for them to reply{fyi_option}
* ACTIONED - Thread is complete

DETAILED CRITERIA:

**TO_REPLY**: The user has received email(s) that require a response. Use this when:
- Someone asks the user a direct question
- Someone requests information or action from the user
- The user needs to provide specific input
- Someone follows up on a conversation requiring the user's response
- There are ANY unanswered questions/requests in the thread that the user hasn't addressed yet
- The user promised to send a follow-up reply, answer, or deliverable back to someone and hasn't sent that follow-up yet
- IMPORTANT: In multi-person threads, track the USER'S specific commitments even if other people are having separate conversations
- CRITICAL: If the user asked a clarifying question AND got an answer BUT still has a pending commitment/deliverable, it's TO_REPLY (not AWAITING_REPLY)

**AWAITING_REPLY**: Waiting for the other person to take action or respond. Use this when:
- The user asked a question and is still waiting for an answer
- The user requested information/action and is still waiting for it to be delivered
- Someone ELSE promised to do something and hasn't done it yet
- The ball is in their court — it's THEIR turn to respond or act
- The user is NOT the one who needs to reply next
- CRITICAL: If the user requested something and then received a response fulfilling that request, the user is NO LONGER awaiting a reply{fyi_criteria}

**ACTIONED**: The thread is complete/done. No further action needed from anyone. Use this when:
- All questions have been answered
- All requests have been fulfilled
- Conversation concluded naturally with acknowledgment or confirmation
- The thread reached a natural conclusion with nothing pending
- The user SENT informational content, recommendations, or helpful resources and isn't waiting for a reply

DELEGATE EDGE CASE:
- If a trusted delegate wrote something equivalent to "je m'en occupe" / "I'll handle it" / "I'll take care of it" in response to a request directed at the user, treat that request as ACTIONED — the delegate took ownership and no further action is needed from the user.

CRITICAL RULES - READ CAREFULLY:
1. **CHECK EVERY MESSAGE**: Don't just look at the latest message. Scan the ENTIRE thread for unanswered questions or pending requests
2. **Unanswered questions persist**: If an earlier message contains an unanswered question or request, and a later message contains only informational content, the status is still determined by the unanswered question/request
3. **Promises from different perspectives**:
   - If SOMEONE ELSE promised to do something -> AWAITING_REPLY (waiting for them)
   - If YOU promised a future reply, answer, or deliverable back to the sender -> TO_REPLY
4. **Multi-person threads**: In threads with multiple participants, focus ONLY on what the user needs to do. Ignore conversations between other people that don't involve the user's commitments.
5. **Request fulfillment**: If the user asked for something and received it, OR another participant fully handled a request involving the user, AND the user has no pending commitments, the status should be {rule_5_tail}.
6. **Clarifying questions don't cancel commitments**: If the user has a pending commitment and asks a clarifying question that gets answered, the status is TO_REPLY (not AWAITING_REPLY).
7. **Taking ownership can fulfill the request**: If the sender asked the user to do something and the user's latest reply takes ownership of it ("I'll handle it", "I'll take care of it"), treat the request as fulfilled and classify ACTIONED unless that reply clearly promises another email update, answer, or deliverable later.
8. **User sends info/recommendations**: When the user SENDS informational content, advice, or recommendations without asking questions or expecting specific actions, it's ACTIONED (not AWAITING_REPLY).
9. **Latest message context matters**: If the latest message is purely informational but there are unresolved items earlier in the thread, prioritize the unresolved items{rule_10}

{thread_block}

Respond with:
- status: One of {status_enum}
- rationale: Brief one-line explanation for the decision
"""
