"""Characterization tests for tools.email_render.md_to_html.

md_to_html is a small line-by-line state machine (tables open and close
depending on what follows them). These tests pin the exact HTML it produces
today, including two quirks that are called out in their names, so that
restructuring the function cannot change the output unnoticed.
"""

import unittest

from tools.email_render import md_to_html

TABLE_OPEN = (
    '<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">\n<thead>'
)
TABLE_CLOSE = "</tbody></table>"


def table(header_cells, *rows):
    head = "<tr>" + "".join(f"<th>{c}</th>" for c in header_cells) + "</tr>"
    body = "".join("\n<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"{TABLE_OPEN}\n{head}\n</thead><tbody>{body}\n{TABLE_CLOSE}"


class MarkdownToHtmlTests(unittest.TestCase):
    def test_empty_string_is_a_single_break(self):
        self.assertEqual(md_to_html(""), "<br>")

    def test_paragraphs_and_blank_lines(self):
        self.assertEqual(md_to_html("one\n\ntwo"), "<p>one</p>\n<br>\n<p>two</p>")

    def test_headings_levels_one_to_three_with_inline_formatting(self):
        text = "# One\n## Two **b**\n### Three [l](https://x.test)"
        self.assertEqual(
            md_to_html(text),
            '<h1>One</h1>\n<h2>Two <strong>b</strong></h2>\n<h3>Three <a href="https://x.test">l</a></h3>',
        )

    def test_fourth_level_heading_is_plain_paragraph_text(self):
        self.assertEqual(md_to_html("#### Four"), "<p>#### Four</p>")

    def test_horizontal_rule_needs_at_least_ten_asterisks(self):
        self.assertEqual(
            md_to_html("before\n" + "*" * 21 + "\nafter"), "<p>before</p>\n<hr>\n<p>after</p>"
        )
        self.assertEqual(md_to_html("*" * 10), "<hr>")
        self.assertNotIn("<hr>", md_to_html("*" * 9))

    def test_list_items_accept_three_bullet_characters(self):
        self.assertEqual(md_to_html("- a\n• b\n· c"), "<li>a</li>\n<li>b</li>\n<li>c</li>")

    def test_dash_without_space_is_not_a_list_item(self):
        self.assertEqual(md_to_html("-notalist"), "<p>-notalist</p>")

    def test_inline_formatting_links_and_unsafe_urls(self):
        text = "**bold** *it* [lnk](https://e.test/a?b=1&c=2) [bad](javascript:alert(1))"
        self.assertEqual(
            md_to_html(text),
            '<p><strong>bold</strong> <em>it</em> <a href="https://e.test/a?b=1&amp;c=2">lnk</a> bad</p>',
        )

    def test_table_with_separator_row(self):
        text = "| A | B |\n|---|:-:|\n| 1 | 2 |\n| 3 | 4 |"
        self.assertEqual(md_to_html(text), table(["A", "B"], ["1", "2"], ["3", "4"]))

    def test_table_without_separator_row(self):
        self.assertEqual(md_to_html("| A | B |\n| 1 | 2 |"), table(["A", "B"], ["1", "2"]))

    def test_header_only_table_with_separator_has_empty_body(self):
        self.assertEqual(md_to_html("| A | B |\n|---|---|"), table(["A", "B"]))

    def test_indented_table_row_still_starts_a_table(self):
        self.assertEqual(md_to_html("  | A | B |\n|---|---|"), table(["A", "B"]))

    def test_escaped_pipe_stays_inside_the_cell(self):
        text = "| A | B |\n|---|---|\n| x \\| y | 2 |"
        self.assertEqual(md_to_html(text), table(["A", "B"], ["x | y", "2"]))

    def test_cells_get_inline_formatting(self):
        text = "| **A** | B |\n|---|---|\n| *i* | [l](https://x.test) |"
        self.assertEqual(
            md_to_html(text),
            table(["<strong>A</strong>", "B"], ["<em>i</em>", '<a href="https://x.test">l</a>']),
        )

    def test_blank_line_closes_table_and_still_emits_a_break(self):
        text = "| A | B |\n|---|---|\n| 1 | 2 |\n\ntext"
        self.assertEqual(md_to_html(text), table(["A", "B"], ["1", "2"]) + "\n<br>\n<p>text</p>")

    def test_heading_closes_table(self):
        text = "| A |\n|---|\n| 1 |\n## H"
        self.assertEqual(md_to_html(text), table(["A"], ["1"]) + "\n<h2>H</h2>")

    def test_horizontal_rule_closes_table(self):
        text = "| A |\n|---|\n| 1 |\n" + "*" * 12 + "\nx"
        self.assertEqual(md_to_html(text), table(["A"], ["1"]) + "\n<hr>\n<p>x</p>")

    def test_table_at_end_of_text_is_closed(self):
        self.assertTrue(md_to_html("| A |\n|---|\n| 1 |").endswith(TABLE_CLOSE))

    def test_two_tables_separated_by_a_blank_line(self):
        text = "| A |\n|---|\n| 1 |\n\n| B |\n|---|\n| 2 |"
        self.assertEqual(md_to_html(text), table(["A"], ["1"]) + "\n<br>\n" + table(["B"], ["2"]))

    def test_pipe_not_at_line_start_is_plain_text(self):
        self.assertEqual(md_to_html("a | b"), "<p>a | b</p>")

    def test_list_item_right_after_table_rows_stays_inside_the_table(self):
        # Current behaviour, pinned on purpose: only a blank line, heading or rule ends a table.
        text = "| A |\n|---|\n| 1 |\n- item"
        self.assertEqual(
            md_to_html(text),
            f"{TABLE_OPEN}\n<tr><th>A</th></tr>\n</thead><tbody>\n<tr><td>1</td></tr>\n<li>item</li>\n{TABLE_CLOSE}",
        )

    def test_mixed_document(self):
        text = "\n".join(
            [
                "# Daily",
                "",
                "intro **text**",
                "| A | B |",
                "|---|---|",
                "| 1 | 2 |",
                "",
                "- point",
                "*" * 21,
                "end",
            ]
        )
        self.assertEqual(
            md_to_html(text),
            "\n".join(
                [
                    "<h1>Daily</h1>",
                    "<br>",
                    "<p>intro <strong>text</strong></p>",
                    table(["A", "B"], ["1", "2"]),
                    "<br>",
                    "<li>point</li>",
                    "<hr>",
                    "<p>end</p>",
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
