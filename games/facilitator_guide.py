"""The facilitator guide Ask Claude reads on every question: how Flexee Marketing Management works,
for a faculty member running it. Quote-worthy figures here are the defaults of the shipped
configuration (the game itself may have been set up differently; the lookups say what it was set
up with)."""

FACILITATOR_GUIDE = """\
FLEXEE MARKETING MANAGEMENT: FACILITATOR GUIDE

What it is
Flexee Marketing Management is a brand-management simulation. Each team is the marketing leadership of Torque, an energy-drink brand with a loyal but ageing base, and runs it for 14 rounds against three fixed competitors it cannot control. The tension at the centre of the game is how fast and how far to move from high-caffeine heritage toward clean functional wellness without abandoning the base that funds the move. A category-wide health scrutiny hits around round 9 (the instructor can set the round); brands that leaned on heritage are hurt more than brands that built wellness credibility.

Rounds
- Rounds 1 to 3 are a listening period: nothing is decided. Teams use focus groups, advisors and research to learn the market.
- Decisions start at round 4, the takeover round. Levers open on a schedule so the game grows: allocation and emphasis at round 4; positioning and channel at round 5; promotion and message at round 6; the functional line extension at round 7; price at round 8. The instructor can change the opening rounds when creating the game.
- A lever that is not open yet cannot be set. A lever that is open stays open to the end.
- Each round has an opening date and a due date. A round closes at 23:59 on its due date in the time zone the instructor chose when creating the game, or earlier when a faculty member presses Advance round. Then results are computed for every team and the next round opens.
- Between the deadline and the moment the close runs, a change is refused rather than quietly accepted. The last round is never closed automatically; a faculty member closes it.
- A team that has not submitted when a round closes plays that round on the average of the teams that did submit (the cohort average), not on the fixed baseline. A team that did not make a forecast repeats its previous forecast.
- A closed round is locked. A faculty member can reopen it and close it again.
- When you reopen a closed round, teams can change their decisions for it until the new due date you set. On a class that is open to students, reopening needs that new due date; without one the round would unlock but every save would still be refused. A reopened round isn't closed again by itself. The new decisions count when the round that's open now closes, and the reopened round's results update then.
- Students get a reminder email about a day before a round closes (about half a round when a round is shorter than two days), once per round, only if their team has not submitted. Test accounts and inactive accounts are skipped.
- Extending a round's due date moves the later rounds with it.

The decision levers
- Budget allocation: a fixed budget of 10 is split across brand building, performance marketing and research. They must add up to the budget exactly. Performance beyond brand erodes brand equity.
- Segment emphasis: five weights over the segments that add up to 1.
- Positioning: 0 to 100 on one axis, from high-caffeine heritage (0) to clean functional wellness (100). Moving fast has costs, because credibility on the wellness end is earned slowly.
- Promotion: 0 to 1. Deep promotion buys volume and teaches customers a lower price; it also adds deal conditioning.
- Price: between 1.10 and 1.90 by default.
- Channel mix: weights over mass retail, direct-to-consumer and social, adding up to 1. Retail reaches the Legacy Core and Mainstream; social reaches Gen-Z.
- Message: Neutral, Performance, Heritage, Clean-energy or Community. Heritage is loved by the loud Legacy Core and tuned out by the growth segments.
- Functional line extension: launch or not, with its own positioning. It can open a new future or cannibalise the base.

The five segments
Legacy Core (loyal, loud, leans heritage, fading), Fitness and Performance (functional, rewards substance), Clean-Wellness (wants functional and clean, says more than it does), Gen-Z Emergent (social-native, understates, deciding the future) and Mainstream (the honest middle; its words and behaviour roughly match). What segments say in a focus group (stated warmth) differs from what they do (revealed warmth); teams only ever see stated warmth.

What teams can use
- Focus group: teams interview chosen customer personas, up to three engagement actions per round across focus groups and advisors.
- Advisors: a panel of six advisors argues for and against a draft decision.
- Market research: stated studies are cheap and carry the say-do bias; behavioural studies cost more and show the truth. Cost comes out of the team's research budget.
- Forecast: each round a team predicts its market share or growth-segment share in percent; accuracy is scored when the round closes and feeds the defense grade.
- Board defense: a written defense of the team's strategy. It is graded in two parts: a deterministic part from decisions and results, and a prose part graded by Claude. Engagement (focus groups and advisor use) counts toward the defense, with diminishing returns.

Scores
Each team gets four scores out of 100 per round: brand health, customer value, market position and financial, combined into an overall score. By default the four weigh the same; the instructor can set different weights when creating the game. Students see their own scores and an anonymous best, average and worst for the cohort; the instructor sees every team and can reveal identities.

Practice games
A faculty member can make a practice copy of a game: one firm of their own against three reference firms (Heritage Defender, Performance Chaser, Wellness Pivoter) that each play a fixed strategy every round. A practice game has no deadlines and no students; rounds close only when the faculty member advances them, and Start over resets it. Practice games do not appear in billing, reminders or reports.

AI
The simulation's numbers are always computed by the engine. Claude only writes the customer and advisor voices, grades the prose of a defense, and answers faculty questions in Ask Claude. Each game has one switch for all of it. With it off, focus groups and advisors use the engine's plain templated voice, the written part of a defense is not graded, and Ask Claude is unavailable. The whole site also has a monthly spending ceiling; when it is reached, AI features pause until the 1st.

Ask Claude
Ask Claude can read, never change. It looks up this game's status, round close history, stored results, each team's decisions and forecast accuracy, and reads this guide. It does not give people's names, does not suggest what a team should decide, and does not write messages for students. Every figure in an answer is checked against the lookups; a sentence with a figure the lookups did not return is removed and the panel says so. Each game can ask 100 questions a month.
"""
