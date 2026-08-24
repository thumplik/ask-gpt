import unittest

from askgpt.secrets import format_findings, scan


class ScanTest(unittest.TestCase):
    def test_detects_openai_key(self):
        findings = scan("token = sk-abcdefghij0123456789ABCD")
        self.assertEqual([f.name for f in findings], ["openai-key"])

    def test_detects_github_pat(self):
        findings = scan("ghp_0123456789abcdefghijABCDEFGHIJ0123")
        self.assertEqual([f.name for f in findings], ["github-pat"])

    def test_detects_aws_access_key(self):
        self.assertTrue(scan("AKIAIOSFODNN7EXAMPLE"))

    def test_detects_bearer_token(self):
        self.assertTrue(scan("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345"))

    def test_detects_private_key_block(self):
        self.assertTrue(scan("-----BEGIN RSA PRIVATE KEY-----"))

    def test_reports_line_numbers(self):
        findings = scan("clean\nclean\nsk-abcdefghij0123456789ABCD")
        self.assertEqual(findings[0].line, 3)

    def test_excerpt_is_redacted(self):
        secret = "sk-abcdefghij0123456789ABCD"
        finding = scan(secret)[0]
        self.assertNotIn(secret, finding.excerpt)
        self.assertIn("...", finding.excerpt)

    def test_detects_slack_token(self):
        findings = scan("xoxb-1234567890-abcdefghijkl")
        self.assertEqual([f.name for f in findings], ["slack-token"])

    def test_reports_every_finding_not_just_the_first(self):
        # Catches an implementation that returns on first match.
        findings = scan("sk-abcdefghij0123456789ABCD\nAKIAIOSFODNN7EXAMPLE")
        self.assertEqual(len(findings), 2)

    def test_excerpt_reports_the_length(self):
        # Without this, _redact can drop the length entirely and the suite
        # stays green -- verified by mutation.
        finding = scan("sk-abcdefghij0123456789ABCD")[0]
        self.assertIn("27", finding.excerpt)

    def test_two_matches_of_one_pattern_on_one_line(self):
        # test_reports_every_finding_not_just_the_first uses two DIFFERENT
        # patterns on two DIFFERENT lines, so `search` instead of `finditer`
        # passes it while silently under-reporting two keys pasted together.
        findings = scan("a=sk-abcdefghij0123456789ABCD b=sk-zyxwvutsrq9876543210ZYXW")
        self.assertEqual(len(findings), 2)

    def test_format_findings_never_prints_the_secret(self):
        # format_findings is what the user actually sees. Untested, it could
        # leak the very secret it is warning about.
        secret = "sk-abcdefghij0123456789ABCD"
        message = format_findings(scan(secret))
        self.assertNotIn(secret, message)
        self.assertIn("line 1", message)
        self.assertIn("openai-key", message)
        self.assertIn("--allow-secrets", message)

    def test_detects_a_jwt(self):
        self.assertTrue(
            scan('t = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J"')
        )

    def test_detects_a_google_api_key(self):
        self.assertTrue(scan('k = "AIzaSyD-abcdefghijklmnopqrstuvwxyz01234"'))

    def test_detects_a_stripe_key(self):
        self.assertTrue(scan('k = "sk_live_abcdefghij0123456789"'))

    def test_detects_an_unfamiliar_format_by_its_variable_name(self):
        # The gap the specific patterns leave: an unknown credential format is
        # unrecognisable by shape, so this keys on the name instead.
        findings = scan('DATABASE_PASSWORD = "Xq7!vbnm234ZZplok"')
        self.assertEqual([f.name for f in findings], ["assigned-credential"])

    def test_assignment_rule_ignores_short_values(self):
        self.assertEqual(scan('password = "abc"'), [])

    def test_assignment_rule_ignores_prose_and_comparisons(self):
        self.assertEqual(scan("the password field is validated on submit"), [])
        self.assertEqual(scan("if user_token == expected: pass"), [])

    def test_clean_text_yields_nothing(self):
        self.assertEqual(scan("def add(a, b):\n    return a + b\n"), [])

    def test_prose_mentioning_sk_is_not_flagged(self):
        self.assertEqual(scan("the sk- prefix identifies OpenAI keys"), [])


if __name__ == "__main__":
    unittest.main()
