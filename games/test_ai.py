"""AI use: the per-game switch, usage logging, the site ceiling, Ask Claude (figure check, lookups,
limits, endpoints) and the voice layers with AI off. No test calls Claude."""
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Role, User
from flexee_engine import api, model_client as mc
from . import ai_use, ask_claude, models
from .ask_text import announces_lookups, compose, figure_check, normalise_figure, without_steps
from .ask_tools import AskTools
from .services import GameService

BASE = "/meridian/api/games"
UTC = dt_timezone.utc


def _game(faculty=None, **kw):
    faculty = faculty or User.objects.create_user(username=f"f{User.objects.count()}", password="x",
                                                  role=Role.FACULTY, email="f@example.com")
    game = models.Game.objects.create(
        name="Torque 101", faculty=faculty, num_rounds=14, takeover_round=4, current_round=4,
        config_snapshot=api.default_config(), timezone="America/New_York",
        status=models.GameStatus.RUNNING, deployed=True, student_deployed=True, **kw)
    game.faculty_members.add(faculty)
    for n in ("Team A", "Team B"):
        models.Team.objects.create(game=game, name=n)
    return game


def _live_client():
    """Stands in for the Anthropic client: records usage like the real one does."""
    def client(system, user, temperature=0.7):
        sink = mc._usage_sink.get()
        if sink is not None:
            sink.append({"model": "claude-haiku-4-5", "input_tokens": 1000, "output_tokens": 200})
        return "Marcus: I'm warm on this."
    return client


class FigureCheckTests(TestCase):
    def test_figures_compare_as_numbers(self):
        self.assertEqual(normalise_figure("$2,800,000"), normalise_figure("2800000"))
        self.assertEqual(normalise_figure("5%"), normalise_figure("5"))
        self.assertEqual(normalise_figure("50.0"), "50")
        self.assertNotEqual(normalise_figure("-50"), normalise_figure("50"))

    def test_a_sentence_with_an_unknown_figure_is_dropped_and_the_rest_kept(self):
        r = figure_check("Team A scored 58.7 overall. Team B scored 91 overall. Both are close.",
                         ["overall 58.7"])
        self.assertEqual(r.answer, "Team A scored 58.7 overall. Both are close.")
        self.assertEqual(r.dropped, [{"sentence": "Team B scored 91 overall.", "figures": ["91"]}])
        self.assertEqual(r.checked, 2)

    def test_figures_in_the_question_and_guide_count_as_known(self):
        r = figure_check("Round 4 is the first decision round.", ["decisions start at round 4"])
        self.assertEqual(r.dropped, [])

    def test_a_quoted_figure_passes_only_if_quotable(self):
        self.assertEqual(len(figure_check('Team A wrote "we sold 900 units".', []).dropped), 1)
        self.assertEqual(figure_check('Team A wrote "we sold 900 units".', [], ["900"]).dropped, [])
        self.assertEqual(len(figure_check("Team A sold 900 units.", [], ["900"]).dropped), 1)

    def test_compose_drops_narration_and_keeps_the_answer(self):
        rounds = ["I'll look up the current status.",
                  "Round 4 is open and closes on Tue, Mar 10 2026 at 23:59 (America/New_York). Team A has submitted and Team B has not yet."]
        self.assertTrue(compose(rounds).startswith("Round 4 is open"))
        self.assertNotIn("I'll look", compose(rounds))

    def test_a_narrating_sentence_with_a_figure_stays(self):
        self.assertIn("round 6", without_steps("Let me check round 6 for you."))

    def test_announcing_is_not_answering(self):
        self.assertTrue(announces_lookups("I'll look at the results now."))
        self.assertTrue(announces_lookups(""))
        self.assertFalse(announces_lookups("Team A leads."))


class PricingAndStateTests(TestCase):
    def test_cost_uses_the_model_price_and_falls_back_to_the_highest(self):
        self.assertEqual(ai_use.call_cost(1_000_000, 1_000_000, "claude-haiku-4-5"), 6.0)
        self.assertEqual(ai_use.call_cost(1_000_000, 0, "claude-sonnet-5-5"), 3.0)
        self.assertEqual(ai_use.call_cost(1_000_000, 0, "some-new-model"), 15.0)

    def test_state_order(self):
        game = _game()
        with patch.object(api, "live_model_available", return_value=False):
            self.assertEqual(ai_use.state(game), "unavailable")
        with patch.object(api, "live_model_available", return_value=True):
            self.assertEqual(ai_use.state(game), "on")
            game.ai_enabled = False
            self.assertEqual(ai_use.state(game), "off_switch")
            game.ai_enabled = True
            ai_use.record_call(game, "ask_claude", cost_usd=ai_use.ceiling_usd())
            self.assertEqual(ai_use.state(game), "off_ceiling")

    def test_spend_is_this_calendar_months_whole_site(self):
        game = _game()
        ai_use.record_call(game, "focus_group", cost_usd=1.25)
        ai_use.record_call(None, "ask_claude", cost_usd=0.5)
        old = models.AiCall.objects.create(feature="advisors", cost_usd=100)
        models.AiCall.objects.filter(pk=old.pk).update(created_at=datetime(2020, 1, 5, tzinfo=UTC))
        self.assertEqual(ai_use.site_spend(), 1.75)

    def test_practice_calls_are_flagged_but_still_count_toward_spend(self):
        game = _game(is_practice=True)
        ai_use.record_call(game, "focus_group", cost_usd=2)
        self.assertTrue(models.AiCall.objects.get().practice)
        self.assertEqual(ai_use.site_spend(), 2.0)


@override_settings(FLEXEE_AI_MONTHLY_CEILING_USD=50)
class VoiceSessionTests(TestCase):
    def setUp(self):
        self.game = _game()

    def test_live_calls_are_measured_and_logged_once_per_feature_use(self):
        with patch.object(api, "live_model_available", return_value=True), \
                patch.object(api, "get_model_client", return_value=_live_client()):
            with ai_use.session(self.game, "focus_group") as ai:
                for _ in range(3):
                    ai.client("sys", "user")
        row = models.AiCall.objects.get()
        self.assertEqual((row.feature, row.outcome, row.game_id_ref, row.course), ("focus_group", "ok", self.game.pk, "Torque 101"))
        self.assertEqual((row.input_tokens, row.output_tokens), (3000, 600))
        self.assertAlmostEqual(row.cost_usd, (3000 * 1 + 600 * 5) / 1e6, places=6)
        self.assertFalse(row.practice)

    def test_a_failed_model_call_is_logged_as_an_error_and_still_raises(self):
        with patch.object(api, "live_model_available", return_value=True), \
                patch.object(api, "get_model_client", return_value=_live_client()):
            with self.assertRaises(mc.ModelCallFailed):
                with ai_use.session(self.game, "advisors"):
                    raise mc.ModelCallFailed("down")
        self.assertEqual(models.AiCall.objects.get().outcome, "error")

    def test_switch_off_gives_no_client_and_logs_nothing(self):
        self.game.ai_enabled = False
        with patch.object(api, "live_model_available", return_value=True):
            with ai_use.session(self.game, "focus_group") as ai:
                self.assertIsNone(ai.client)
                self.assertEqual(ai.state, "off_switch")
        self.assertEqual(models.AiCall.objects.count(), 0)

    def test_switch_wins_even_without_a_key(self):
        self.game.ai_enabled = False
        with patch.object(api, "live_model_available", return_value=False):
            with ai_use.session(self.game, "focus_group") as ai:
                self.assertIsNone(ai.client)

    def test_ceiling_reached_gives_no_client(self):
        ai_use.record_call(self.game, "ask_claude", cost_usd=50)
        with patch.object(api, "live_model_available", return_value=True):
            with ai_use.session(self.game, "advisors") as ai:
                self.assertIsNone(ai.client)
                self.assertEqual(ai.state, "off_ceiling")

    def test_no_key_uses_the_offline_client_and_logs_nothing(self):
        with patch.object(api, "live_model_available", return_value=False):
            with ai_use.session(self.game, "focus_group") as ai:
                self.assertIsNotNone(ai.client)
        self.assertEqual(models.AiCall.objects.count(), 0)


class VoiceFeaturesWithAiOffTests(TestCase):
    """The real service methods, with the game's switch off: no model client reaches the engine."""

    def setUp(self):
        self.game = _game(ai_enabled=False)
        self.team = self.game.teams.get(name="Team A")
        GameService(self.game).close_round(4)               # a closed round to talk about

    def test_focus_group_uses_the_engines_own_voice(self):
        seen = {}
        real = api.focus_group

        def spy(*a, **kw):
            seen["client"] = kw.get("model_client", "missing")
            return real(*a, **kw)
        pid = api.persona_pool()[0]["id"]
        with patch.object(api, "focus_group", side_effect=spy):
            out = GameService(self.game).focus_group(4, self.team, [pid])
        self.assertIsNone(seen["client"])
        self.assertTrue(out)
        self.assertEqual(models.AiCall.objects.count(), 0)

    def test_advisors_use_the_engines_own_voice(self):
        seen = {}
        real = api.advise

        def spy(*a, **kw):
            seen["client"] = kw.get("model_client", "missing")
            return real(*a, **kw)
        with patch.object(api, "advise", side_effect=spy):
            base = api.default_config()["baseline_decision"]
            stances = GameService(self.game).consult_advisors(
                4, self.team, {"round": 4, "levers": {**base, "brand": 6.0, "perf": 2.0, "research": 2.0},
                               "baseline": base})
        self.assertIsNone(seen["client"])
        self.assertTrue(stances)

    def test_the_written_defense_is_not_graded_but_the_decisions_are(self):
        with patch.object(api, "grade_written_defense") as grader:
            out = GameService(self.game).board_defense(self.team, "We held the line.")
        grader.assert_not_called()
        self.assertTrue(out["ai_off"])
        self.assertEqual(out["prose"], {})
        self.assertIn("deterministic", out)

    def test_with_the_switch_on_the_defense_is_graded(self):
        self.game.ai_enabled = True
        graded = {"prose_dimensions": {"argument_coherence": 70.0, "honest_evidence_citation": 70.0,
                                       "ownership_of_tradeoffs": 70.0},
                  "prose_overall": 70.0, "justification": "ok", "cache_key": "k", "cached": False}
        with patch.object(api, "live_model_available", return_value=False), \
                patch.object(api, "grade_written_defense", return_value=graded) as grader:
            out = GameService(self.game).board_defense(self.team, "We held the line.")
        grader.assert_called_once()
        self.assertNotIn("ai_off", out)
        self.assertEqual(out["prose"]["prose_overall"], 70.0)


class AskToolsTests(TestCase):
    def setUp(self):
        self.game = _game()
        self.svc = GameService(self.game)
        self.a = self.game.teams.get(name="Team A")
        student = User.objects.create_user(username="stu", password="x", role=Role.STUDENT,
                                           first_name="Secret", last_name="Person")
        self.a.users.add(student)
        self.svc.submit(4, self.a, {"brand": 6, "perf": 2, "research": 2, "emphasis": [0.4, 0.15, 0.05, 0.1, 0.3]})
        self.svc.close_round(4)
        self.tools = AskTools(self.game, now=datetime(2026, 3, 9, 12, tzinfo=UTC))

    def test_status_names_teams_and_counts_but_never_people(self):
        out = self.tools.run("get_game_status", {})
        self.assertEqual(out["phase"], "running")
        self.assertEqual(out["round_now"]["round"], 5)
        self.assertEqual(out["round_now"]["of"], 14)
        self.assertEqual({t["team"] for t in out["teams"]}, {"Team A", "Team B"})
        self.assertEqual(next(t for t in out["teams"] if t["team"] == "Team A")["students"], 1)
        self.assertNotIn("Secret", str(out))
        self.assertNotIn("stu", str(out).replace("students", ""))

    def test_status_gives_the_deadline_written_out(self):
        from .schedule import build_schedule
        from datetime import date
        self.game.schedule = build_schedule(date(2099, 3, 4), 7, 14)
        self.game.current_round = 1
        out = AskTools(self.game, now=datetime(2099, 3, 5, 12, tzinfo=UTC)).status()
        self.assertEqual(out["round_now"]["closes"], "Tue, Mar 10 2099 at 23:59 (America/New_York)")
        self.assertIn("day", out["round_now"]["time_left"])

    def test_close_history(self):
        out = self.tools.run("get_close_history", {})
        self.assertEqual(len(out["closes"]), 1)
        self.assertEqual(out["closes"][0]["round"], 4)
        self.assertEqual(out["closes"][0]["how"], "by a faculty member pressing Advance")
        self.assertEqual(out["closes"][0]["teams_submitted"], 1)

    def test_results_default_to_the_latest_closed_round_and_every_team(self):
        out = self.tools.run("get_results", {"measures": ["overall", "rank", "market_share"]})
        self.assertEqual({r["team"] for r in out["results"]}, {"Team A", "Team B"})
        row = out["results"][0]["rounds"][0]
        self.assertEqual(row["round"], 4)
        self.assertIn("overall score", row)
        self.assertIn("rank among teams (1 is best)", row)
        self.assertNotIn("BigRed", str(out))                    # the engine's backdrop firms are not teams

    def test_results_before_any_close_say_so(self):
        game = _game()
        out = AskTools(game).run("get_results", {})
        self.assertEqual(out["results"], [])
        self.assertIn("No round has closed", out["note"])

    def test_show_table_returns_one_view_per_answer(self):
        self.tools.run("show_table", {"measures": ["overall"]})
        self.tools.run("show_table", {"measures": ["financial"]})
        self.assertEqual(len(self.tools.views), 1)
        self.assertEqual(self.tools.views[0]["columns"][:3], ["team", "round", "overall score"])

    def test_decisions_in_words(self):
        out = self.tools.run("get_decisions", {"team": "team a", "round": 4})
        self.assertTrue(out["submitted"])
        self.assertIn("brand 6", out["decisions"]["budget split"])
        self.assertIn("Legacy Core 40%", out["decisions"]["segment emphasis"])
        none = self.tools.run("get_decisions", {"team": "Team B", "round": 4})
        self.assertFalse(none["submitted"])
        self.assertIn("cohort average", none["note"])

    def test_unknown_team_and_tool(self):
        self.assertIn("error", self.tools.run("get_decisions", {"team": "Nobody", "round": 4}))
        self.assertIn("error", self.tools.run("delete_everything", {}))
        self.assertIn("error", self.tools.run("get_team_detail", {"team": "Nobody"}))

    def test_it_only_sees_its_own_game(self):
        other = _game()
        models.Team.objects.create(game=other, name="Elsewhere")
        self.assertNotIn("Elsewhere", str(self.tools.run("get_game_status", {})))

    def test_forecast_accuracy_in_percent(self):
        models.Forecast.objects.create(game=self.game, round=4, team=self.a, metric="own_share",
                                       predicted=30.0, actual=25.0, accuracy=0.8)
        out = self.tools.run("get_forecast_accuracy", {})
        self.assertEqual(out["forecasts"][0]["accuracy_pct"], 80)
        self.assertEqual(out["forecasts"][0]["predicted_pct"], 30)


def _reply(*blocks, stop="end_turn", usage=None):
    return {"content": list(blocks), "stop_reason": stop,
            "usage": usage or {"input_tokens": 1000, "output_tokens": 100}}


def _text(t):
    return {"type": "text", "text": t}


def _use(name, **inp):
    return {"type": "tool_use", "id": f"tu_{name}", "name": name, "input": inp}


@override_settings(FLEXEE_ASK_QUESTIONS_PER_MONTH=3, FLEXEE_AI_MONTHLY_CEILING_USD=50)
class AskClaudeTests(TestCase):
    def setUp(self):
        self.faculty = User.objects.create_user(username="fac", password="x", role=Role.FACULTY, email="f@example.com")
        self.game = _game(self.faculty)
        GameService(self.game).close_round(4)
        self.ctx = {"page": "overview", "label": "Overview", "details": {}}
        patcher = patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, replies, question="Where do we stand?", conversation="c1", **kw):
        queue = list(replies)
        seen = []

        def fake(cfg, system, tools, messages):
            seen.append(list(messages))
            return queue.pop(0)
        out = ask_claude.ask(user=self.faculty, game=self.game, context=self.ctx, question=question,
                             conversation_id=conversation, send_fn=fake, **kw)
        return out, seen

    def test_a_lookup_then_an_answer(self):
        out, seen = self.ask([
            _reply(_use("get_game_status"), stop="tool_use"),
            _reply(_text("Round 5 is open out of 14 rounds, and both teams are still to submit.")),
        ])
        self.assertIn("Round 5 is open", out["answer"])
        self.assertEqual(out["dropped"], [])
        self.assertEqual(out["lookups"], [{"name": "get_game_status", "summary": "where the game stands: round 5 open"}])
        self.assertEqual(out["questions_left"], 2)
        q = models.AskQuestion.objects.get()
        self.assertEqual((q.status, q.game_id, q.user_id, q.conversation_id), ("answered", self.game.pk, self.faculty.pk, "c1"))
        self.assertEqual((q.input_tokens, q.output_tokens), (2000, 200))
        call = models.AiCall.objects.get()
        self.assertEqual((call.feature, call.outcome, call.game_id_ref), ("ask_claude", "ok", self.game.pk))
        self.assertEqual(call.input_tokens, 2000)
        self.assertGreater(call.cost_usd, 0)

    def test_a_figure_that_no_lookup_returned_is_dropped(self):
        out, _ = self.ask([
            _reply(_use("get_game_status"), stop="tool_use"),
            _reply(_text("Round 5 is open. Team A has 777 points.")),
        ])
        self.assertEqual(out["answer"], "Round 5 is open.")
        self.assertEqual(out["dropped"][0]["figures"], ["777"])
        self.assertEqual(models.AiCall.objects.get().sentences_dropped, 1)

    def test_an_answer_of_only_unknown_figures_is_replaced_by_the_refusal(self):
        out, _ = self.ask([_reply(_text("Team A has 777 points."))])
        self.assertEqual(out["answer"], ask_claude.REFUSALS["all_dropped"])

    def test_a_reply_that_only_announces_lookups_is_sent_back_once(self):
        out, seen = self.ask([
            _reply(_text("Let me check the status.")),
            _reply(_use("get_game_status"), stop="tool_use"),
            _reply(_text("Round 5 is open for both teams to decide on.")),
        ])
        self.assertIn("Round 5 is open", out["answer"])
        self.assertEqual(seen[1][-1]["content"], "Go ahead: use the lookups now, then answer the question.")

    def test_a_lookups_empty_input_goes_back_as_an_object(self):
        _, seen = self.ask([
            _reply({"type": "tool_use", "id": "t", "name": "get_game_status", "input": []}, stop="tool_use"),
            _reply(_text("Round 5 is open and decisions are due from both teams.")),
        ])
        self.assertEqual(seen[1][-2]["content"][0]["input"], {})

    def test_the_table_comes_back_beside_the_answer(self):
        out, _ = self.ask([
            _reply(_use("show_table", measures=["overall"]), stop="tool_use"),
            _reply(_text("The table below shows the overall scores at the latest closed round.")),
        ])
        self.assertEqual(len(out["tables"]), 1)

    def test_earlier_turns_of_a_conversation_go_back(self):
        self.ask([_reply(_text("Both teams play in a game with 14 rounds."))], question="How many rounds?")
        _, seen = self.ask([_reply(_text("It has 14 rounds in all."))], question="And again?")
        self.assertEqual(seen[0][0], {"role": "user", "content": "How many rounds?"})
        self.assertEqual(seen[0][1]["role"], "assistant")
        self.assertEqual(seen[0][-1], {"role": "user", "content": "And again?"})
        # a different conversation starts clean
        _, seen = self.ask([_reply(_text("It has 14 rounds in all."))], question="Fresh?", conversation="c2")
        self.assertEqual(len(seen[0]), 1)

    def test_the_monthly_allowance_per_game(self):
        for _ in range(3):
            self.ask([_reply(_text("It has 14 rounds in all."))])
        with self.assertRaisesMessage(ask_claude.AskRefused, "used all its questions"):
            self.ask([_reply(_text("x"))])
        other = _game(self.faculty)                              # another game has its own allowance
        out = ask_claude.ask(user=self.faculty, game=other, context=self.ctx, question="q", conversation_id="",
                             send_fn=lambda *a: _reply(_text("It has 14 rounds in all.")))
        self.assertEqual(out["questions_left"], 2)

    def test_a_failed_question_does_not_count(self):
        def boom(*a):
            raise RuntimeError("down")
        with self.assertRaises(ask_claude.AskRefused) as cm:
            ask_claude.ask(user=self.faculty, game=self.game, context=self.ctx, question="q",
                           conversation_id="c", send_fn=boom)
        self.assertTrue(cm.exception.unreachable)
        self.assertEqual(models.AskQuestion.objects.get().status, "failed")
        self.assertEqual(models.AiCall.objects.get().outcome, "error")
        self.assertEqual(ask_claude.questions_left(self.game), 3)

    def test_refusals(self):
        with self.assertRaisesMessage(ask_claude.AskRefused, "Ask a question of up to"):
            self.ask([], question="   ")
        with self.assertRaisesMessage(ask_claude.AskRefused, "Ask a question of up to"):
            self.ask([], question="x" * 1001)
        self.game.ai_enabled = False
        with self.assertRaisesMessage(ask_claude.AskRefused, "off for this simulation"):
            self.ask([])
        self.game.ai_enabled = True
        ai_use.record_call(self.game, "focus_group", cost_usd=50)
        with self.assertRaisesMessage(ask_claude.AskRefused, "limit for the whole site"):
            self.ask([])

    def test_not_set_up_without_a_key(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaisesMessage(ask_claude.AskRefused, "isn't set up"):
                self.ask([])

    def test_old_questions_are_deleted_after_90_days(self):
        old = models.AskQuestion.objects.create(game=self.game, question="old", answer="a")
        models.AskQuestion.objects.filter(pk=old.pk).update(created_at=datetime(2020, 1, 1, tzinfo=UTC))
        self.ask([_reply(_text("It has 14 rounds in all."))])
        self.assertFalse(models.AskQuestion.objects.filter(pk=old.pk).exists())


@override_settings(FLEXEE_ASK_QUESTIONS_PER_MONTH=100)
class AskEndpointTests(TestCase):
    def setUp(self):
        self.faculty = User.objects.create_user(username="fac", password="x", role=Role.FACULTY)
        self.game = _game(self.faculty)
        self.client = APIClient()
        self.client.force_authenticate(self.faculty)
        patcher = patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_status(self):
        r = self.client.get(f"{BASE}/{self.game.id}/ask/").json()
        self.assertEqual(r, {"state": "on", "enabled": True, "questions_left": 100, "allowance": 100})

    def test_the_switch(self):
        r = self.client.patch(f"{BASE}/{self.game.id}/ai/", {"enabled": False}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["enabled"], r.json()["state"]), (False, "off_switch"))
        self.game.refresh_from_db()
        self.assertFalse(self.game.ai_enabled)
        bad = self.client.patch(f"{BASE}/{self.game.id}/ai/", {}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_a_question_and_its_refusals(self):
        body = {"question": "Where do we stand?", "conversation_id": "c1",
                "context": {"page": "overview", "label": "Overview", "details": {"a": "b", "n": 3}}}
        with patch.object(ask_claude, "send", return_value=_reply(_text("It has 14 rounds in all."))):
            ok = self.client.post(f"{BASE}/{self.game.id}/ask/", body, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()["answer"], "It has 14 rounds in all.")
        self.assertEqual(models.AskQuestion.objects.get().context["details"], {"a": "b"})   # only strings kept
        self.client.patch(f"{BASE}/{self.game.id}/ai/", {"enabled": False}, format="json")
        off = self.client.post(f"{BASE}/{self.game.id}/ask/", body, format="json")
        self.assertEqual(off.status_code, 400)
        self.assertIn("off for this simulation", off.json()["detail"])

    def test_unreachable_is_a_503(self):
        body = {"question": "q", "context": {"page": "overview", "label": "Overview"}}
        with patch.object(ask_claude, "send", side_effect=RuntimeError("down")):
            r = self.client.post(f"{BASE}/{self.game.id}/ask/", body, format="json")
        self.assertEqual(r.status_code, 503)

    def test_students_and_other_faculty_are_refused(self):
        student = User.objects.create_user(username="stu", password="x", role=Role.STUDENT)
        c = APIClient(); c.force_authenticate(student)
        self.assertEqual(c.get(f"{BASE}/{self.game.id}/ask/").status_code, 403)
        self.assertEqual(c.patch(f"{BASE}/{self.game.id}/ai/", {"enabled": False}, format="json").status_code, 403)
        other = User.objects.create_user(username="fac2", password="x", role=Role.FACULTY)
        c = APIClient(); c.force_authenticate(other)
        self.assertEqual(c.get(f"{BASE}/{self.game.id}/ask/").status_code, 404)
        self.assertEqual(c.patch(f"{BASE}/{self.game.id}/ai/", {"enabled": False}, format="json").status_code, 404)
