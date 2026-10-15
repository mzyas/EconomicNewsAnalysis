"""Tests for market-mover news: selection, fetching, node wiring and briefing."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.briefing_builder import (
    _item_category,
    build_daily_briefing,
    build_daily_briefing_html,
    build_daily_briefing_model,
)
from tools.feed_fetcher_core import FeedItem
from tools.graph.nodes import movers_news_node, rank_items_node, trend_table_node
from tools.movers import (
    build_query_url,
    describe_trigger,
    fetch_mover_items,
    select_movers,
)


def _snap(symbol_id="nvda", name="NVIDIA", day=-6.2, week=-9.1, **overrides):
    snap = {
        "symbol_id": symbol_id,
        "ticker": symbol_id.upper(),
        "name": name,
        "asset_class": "equity_stock",
        "asset_type": "price",
        "region": "US",
        "status": "success",
        "change_pct": day,
        "change_1w_pct": week,
    }
    snap.update(overrides)
    return snap


def _feed_item(title="NVIDIA shares slide", link="https://news.google.com/rss/articles/abc"):
    return FeedItem(
        source_id="google_news_mover_nvda",
        source_name="Google News: NVIDIA",
        title=title,
        link=link,
        summary="",
        published="2026-07-30T14:00:00+00:00",
        fetched_at="2026-07-31T00:00:00+00:00",
        item_hash="h-" + title[:8],
        source_tags=["market_mover"],
        source_country="US",
        source_language="en",
        source_category="market_mover",
    )


class SelectMoversTests(unittest.TestCase):
    def test_day_move_over_threshold_is_selected(self):
        movers = select_movers([_snap(day=-4.0, week=0.5)], {})
        self.assertEqual([m["symbol_id"] for m in movers], ["nvda"])

    def test_week_move_over_threshold_is_selected(self):
        movers = select_movers([_snap(day=0.3, week=-8.0)], {})
        self.assertEqual(len(movers), 1)

    def test_small_moves_are_ignored(self):
        self.assertEqual(select_movers([_snap(day=3.9, week=7.9)], {}), [])

    def test_yield_failed_and_other_asset_classes_are_ignored(self):
        snaps = [
            _snap("us10y", asset_type="yield", asset_class="bond_yield"),
            _snap("btc", asset_class="crypto"),
            _snap("bad", status="failed"),
        ]
        self.assertEqual(select_movers(snaps, {}), [])

    def test_missing_change_values_do_not_raise(self):
        self.assertEqual(select_movers([_snap(day=None, week=None)], {}), [])

    def test_largest_moves_first_and_capped(self):
        snaps = [
            _snap("a", day=-4.5, week=None),
            _snap("b", day=-9.0, week=None),
            _snap("c", day=-6.0, week=None),
        ]
        movers = select_movers(snaps, {"movers": {"max_symbols": 2}})
        self.assertEqual([m["symbol_id"] for m in movers], ["b", "c"])

    def test_thresholds_are_configurable(self):
        snap = _snap(day=-2.0, week=None)
        self.assertEqual(select_movers([snap], {}), [])
        self.assertEqual(
            len(select_movers([snap], {"movers": {"day_threshold_pct": 2.0}})), 1
        )


class QueryTests(unittest.TestCase):
    def test_stock_query_quotes_name_and_limits_window(self):
        url = build_query_url(_snap())
        self.assertIn("news.google.com/rss/search?q=", url)
        self.assertIn("%22NVIDIA%22%20stock%20when%3A2d", url)

    def test_index_query_has_no_stock_suffix(self):
        url = build_query_url(_snap("sp500", "S&P 500", asset_class="equity_index"))
        self.assertNotIn("stock", url)
        self.assertIn("S%26P%20500", url)

    def test_trigger_text(self):
        self.assertEqual(describe_trigger(_snap()), "NVIDIA 日 -6.2% / 周 -9.1%")


class FetchMoverItemsTests(unittest.TestCase):
    def test_items_are_normalized_and_tagged_with_trigger(self):
        with patch("tools.movers.fetch_rss", return_value=([_feed_item()], None)) as mock:
            items, errors = fetch_mover_items([_snap()], {}, 5, 5)
        self.assertEqual(errors, [])
        self.assertEqual(mock.call_count, 1)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["source"]["category"], "market_mover")
        self.assertEqual(item["discovery"]["channel"], "google_news")
        self.assertEqual(item["mover"]["symbol_id"], "nvda")
        self.assertIn("-6.2%", item["mover"]["trigger"])
        self.assertEqual(_item_category(item), "movers")

    def test_fetch_error_is_recorded_not_raised(self):
        with patch("tools.movers.fetch_rss", return_value=([], "HTTP 503")):
            items, errors = fetch_mover_items([_snap()], {}, 5, 5)
        self.assertEqual(items, [])
        self.assertEqual(errors[0]["type"], "MoverFetchError")
        self.assertIn("503", errors[0]["message"])

    def test_no_movers_means_no_network(self):
        with patch("tools.movers.fetch_rss") as mock:
            items, errors = fetch_mover_items([_snap(day=0.1, week=0.1)], {}, 5, 5)
        mock.assert_not_called()
        self.assertEqual((items, errors), ([], []))


class MoversNodeTests(unittest.TestCase):
    def _state(self, **overrides):
        state = {
            "mode": "briefing",
            "config": {"fetch_enabled": True},
            "market_context": {"snapshots": [_snap()]},
            "items": [{"id": "existing", "title": "x"}],
        }
        state.update(overrides)
        return state

    def test_adds_items_and_keeps_existing(self):
        with patch("tools.movers.fetch_rss", return_value=([_feed_item()], None)):
            update = movers_news_node(self._state())
        ids = [item["id"] for item in update["items"]]
        self.assertEqual(ids[0], "existing")
        self.assertEqual(len(ids), 2)

    def test_skips_when_fetch_disabled_mode_or_flag_off(self):
        for state in (
            self._state(config={"fetch_enabled": False}),
            self._state(mode="trend"),
            self._state(config={"fetch_enabled": True, "movers": {"enabled": False}}),
        ):
            with patch("tools.movers.fetch_rss") as mock:
                update = movers_news_node(state)
            mock.assert_not_called()
            self.assertNotIn("items", update)

    def test_exception_is_recorded_and_items_untouched(self):
        with patch("tools.graph.nodes.fetch_mover_items", side_effect=RuntimeError("boom")):
            update = movers_news_node(self._state())
        self.assertNotIn("items", update)
        self.assertEqual(update["errors"][-1]["type"], "MoverError")


class TrendTableFilterTests(unittest.TestCase):
    def test_stocks_are_hidden_from_trend_table(self):
        index = _snap("sp500", "S&P 500", asset_class="equity_index", day=0.5, week=1.0)
        state = {"market_context": {"snapshots": [_snap(), index]}}
        update = trend_table_node(state)
        symbols = [row["symbol_id"] for row in update["trend_table"]["rows"]]
        self.assertEqual(symbols, ["sp500"])


class RankAndBriefingTests(unittest.TestCase):
    def _mover_item(self):
        with patch("tools.movers.fetch_rss", return_value=([_feed_item()], None)):
            items, _ = fetch_mover_items([_snap()], {}, 5, 5)
        return items[0]

    def test_rank_puts_movers_in_their_own_quota(self):
        item = self._mover_item()
        update = rank_items_node({"config": {"graph": {"movers_max_items": 1}}, "items": [item]})
        self.assertEqual(len(update["ranked_items"]), 1)
        self.assertEqual(_item_category(update["ranked_items"][0]), "movers")

    def test_mover_never_leaks_into_official_section(self):
        model = build_daily_briefing_model([self._mover_item()], [])
        self.assertEqual(len(model["movers"]), 1)
        self.assertEqual(model["official"], [])
        self.assertEqual(model["google"], [])
        self.assertIn("-6.2%", model["movers"][0]["topic"])

    def test_markdown_and_html_render_section_only_when_present(self):
        item = self._mover_item()
        self.assertIn("📉 异动解读", build_daily_briefing([item], []))
        self.assertIn("📉 异动解读", build_daily_briefing_html([item], []))
        self.assertNotIn("📉 异动解读", build_daily_briefing([], []))
        self.assertNotIn("📉 异动解读", build_daily_briefing_html([], []))


if __name__ == "__main__":
    unittest.main()
